"""
eon.workers.task_queue
=========================
TaskQueue durable con backend SQLite.

Estado de una Task en la cola:
    PENDING → RUNNING → COMPLETED / FAILED / CANCELLED

Características:
- Idempotencia: `task_id` es PRIMARY KEY. Si se encola dos veces la misma
  task, la segunda se ignora.
- Reintentos: `attempts` cuenta los intentos. Si `attempts < max_retries`,
  una Task FAILED vuelve a PENDING con backoff exponencial.
- Lease: cuando un Worker coge una Task, se marca `leased_by` y
  `leased_until`. Si el lease expira, la Task vuelve a PENDING.
- Persistencia: sobrevive reinicios de proceso (SQLite).

Uso:
    from eon.workers.task_queue import SQLiteTaskQueue

    queue = SQLiteTaskQueue(engine)
    queue.enqueue(TaskEntry(task_id="t1", capability_id="code.write", payload={...}))
    entry = queue.lease(worker_id="w1", timeout_seconds=30)
    queue.complete("t1", result={"ok": True})
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from ..persistence.sqlite_engine import SQLiteEngine


def _ahora() -> datetime:
    return datetime.now(UTC)


class TaskQueueState(str, Enum):
    """Estado de una Task en la cola."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def es_terminal(self) -> bool:
        return self in (TaskQueueState.COMPLETED, TaskQueueState.FAILED, TaskQueueState.CANCELLED)

    @property
    def es_reintentable(self) -> bool:
        """Solo FAILED puede reintentar (no CANCELLED ni COMPLETED)."""
        return self is TaskQueueState.FAILED


@dataclass
class TaskEntry:
    """Entrada durable en la TaskQueue."""

    task_id: str
    capability_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    estado: TaskQueueState = TaskQueueState.PENDING
    attempts: int = 0
    max_retries: int = 2
    timeout_seconds: float = 30.0
    leased_by: str | None = None
    leased_until: datetime | None = None
    last_error: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_ahora)
    updated_at: datetime = field(default_factory=_ahora)
    available_at: datetime = field(default_factory=_ahora)  # para backoff

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "capability_id": self.capability_id,
            "payload": self.payload,
            "estado": self.estado.value,
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "leased_by": self.leased_by,
            "leased_until": self.leased_until.isoformat() if self.leased_until else None,
            "last_error": self.last_error,
            "result": self.result,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "available_at": self.available_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskEntry:
        def _parse_dt(v: Any) -> datetime | None:
            if v is None or isinstance(v, datetime):
                return v
            try:
                return datetime.fromisoformat(v)
            except (ValueError, TypeError):
                return None

        return cls(
            task_id=d["task_id"],
            capability_id=d["capability_id"],
            payload=d.get("payload", {}),
            estado=TaskQueueState(d.get("estado", "pending")),
            attempts=d.get("attempts", 0),
            max_retries=d.get("max_retries", 2),
            timeout_seconds=d.get("timeout_seconds", 30.0),
            leased_by=d.get("leased_by"),
            leased_until=_parse_dt(d.get("leased_until")),
            last_error=d.get("last_error"),
            result=d.get("result"),
            created_at=_parse_dt(d.get("created_at")) or _ahora(),
            updated_at=_parse_dt(d.get("updated_at")) or _ahora(),
            available_at=_parse_dt(d.get("available_at")) or _ahora(),
        )


class SQLiteTaskQueue:
    """Cola durable de Tasks con backend SQLite.

    Features:
    - Idempotencia: task_id es PK (INSERT OR IGNORE)
    - Lease con expiración: tasks cuyo lease expiró vuelven a PENDING
    - Backoff exponencial: reintentos con available_at diferido
    - Recuperación: sobrevive reinicios de proceso
    """

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._init_schema()

    def _init_schema(self) -> None:
        self._engine.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_queue (
                task_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                capability_id TEXT NOT NULL,
                estado TEXT NOT NULL,
                available_at TEXT NOT NULL,
                leased_by TEXT,
                leased_until TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tq_estado ON task_queue(estado);
            CREATE INDEX IF NOT EXISTS idx_tq_capability ON task_queue(capability_id);
            CREATE INDEX IF NOT EXISTS idx_tq_available ON task_queue(available_at);
            """
        )

    def enqueue(self, entry: TaskEntry) -> bool:
        """Encola una Task. Idempotente: si ya existe, no hace nada.

        Returns:
            True si se encoló, False si ya existía.
        """
        data_json = json.dumps(entry.to_dict(), ensure_ascii=False, default=str)
        now = entry.created_at.isoformat()
        try:
            self._engine.execute(
                "INSERT INTO task_queue (task_id, data, created_at, updated_at, "
                "capability_id, estado, available_at, leased_by, leased_until) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.task_id,
                    data_json,
                    now,
                    now,
                    entry.capability_id,
                    entry.estado.value,
                    entry.available_at.isoformat(),
                    entry.leased_by,
                    entry.leased_until.isoformat() if entry.leased_until else None,
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False  # Ya existe (idempotencia)

    def lease(
        self,
        worker_id: str,
        capability_id: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> TaskEntry | None:
        """Toma una Task PENDING para ejecución.

        Atomicidad: marca la Task como RUNNING y asigna leased_by + leased_until.
        Si capability_id se especifica, solo toma tasks de esa capability.

        Returns:
            TaskEntry leased, o None si no hay tasks disponibles.
        """
        now = _ahora()
        now_iso = now.isoformat()
        lease_until = now + timedelta(seconds=timeout_seconds)

        # Buscar task disponible
        if capability_id:
            row = self._engine.query_one(
                "SELECT data FROM task_queue "
                "WHERE estado = ? AND available_at <= ? AND capability_id = ? "
                "ORDER BY available_at ASC LIMIT 1",
                (TaskQueueState.PENDING.value, now_iso, capability_id),
            )
        else:
            row = self._engine.query_one(
                "SELECT data FROM task_queue WHERE estado = ? AND available_at <= ? ORDER BY available_at ASC LIMIT 1",
                (TaskQueueState.PENDING.value, now_iso),
            )

        if row is None:
            return None

        entry = TaskEntry.from_dict(json.loads(row["data"]))
        entry.estado = TaskQueueState.RUNNING
        entry.leased_by = worker_id
        entry.leased_until = lease_until
        entry.attempts += 1
        entry.updated_at = now

        self._update(entry)
        return entry

    def complete(self, task_id: str, result: dict[str, Any] | None = None) -> bool:
        """Marca una Task como COMPLETED."""
        entry = self.get(task_id)
        if entry is None:
            return False
        entry.estado = TaskQueueState.COMPLETED
        entry.result = result
        entry.leased_by = None
        entry.leased_until = None
        entry.updated_at = _ahora()
        self._update(entry)
        return True

    def fail(
        self,
        task_id: str,
        error: str,
        backoff_base: float = 0.1,
        backoff_max: float = 10.0,
    ) -> bool:
        """Marca una Task como FAILED.

        Si le quedan reintentos, vuelve a PENDING con backoff exponencial.
        Returns True si la Task existe.
        """
        entry = self.get(task_id)
        if entry is None:
            return False

        entry.last_error = error
        entry.updated_at = _ahora()

        if entry.attempts < entry.max_retries + 1:
            # Reintentar con backoff exponencial
            backoff = min(backoff_base * (2 ** (entry.attempts - 1)), backoff_max)
            entry.estado = TaskQueueState.PENDING
            entry.available_at = _ahora() + timedelta(seconds=backoff)
            entry.leased_by = None
            entry.leased_until = None
        else:
            entry.estado = TaskQueueState.FAILED

        self._update(entry)
        return True

    def cancel(self, task_id: str) -> bool:
        """Marca una Task como CANCELLED."""
        entry = self.get(task_id)
        if entry is None:
            return False
        entry.estado = TaskQueueState.CANCELLED
        entry.leased_by = None
        entry.leased_until = None
        entry.updated_at = _ahora()
        self._update(entry)
        return True

    def get(self, task_id: str) -> TaskEntry | None:
        row = self._engine.query_one("SELECT data FROM task_queue WHERE task_id = ?", (task_id,))
        if row is None:
            return None
        return TaskEntry.from_dict(json.loads(row["data"]))

    def list_pending(self) -> list[TaskEntry]:
        rows = self._engine.query_all(
            "SELECT data FROM task_queue WHERE estado = ? ORDER BY available_at",
            (TaskQueueState.PENDING.value,),
        )
        return [TaskEntry.from_dict(json.loads(r["data"])) for r in rows]

    def list_by_state(self, estado: TaskQueueState) -> list[TaskEntry]:
        rows = self._engine.query_all(
            "SELECT data FROM task_queue WHERE estado = ? ORDER BY created_at",
            (estado.value,),
        )
        return [TaskEntry.from_dict(json.loads(r["data"])) for r in rows]

    def list_all(self) -> list[TaskEntry]:
        rows = self._engine.query_all("SELECT data FROM task_queue ORDER BY created_at")
        return [TaskEntry.from_dict(json.loads(r["data"])) for r in rows]

    def recover_expired_leases(self) -> int:
        """Recupera Tasks cuyo lease expiró (RUNNING → PENDING).

        Returns:
            Número de leases recuperados.
        """
        now_iso = _ahora().isoformat()
        rows = self._engine.query_all(
            "SELECT data FROM task_queue WHERE estado = ? AND leased_until IS NOT NULL AND leased_until < ?",
            (TaskQueueState.RUNNING.value, now_iso),
        )
        count = 0
        for row in rows:
            entry = TaskEntry.from_dict(json.loads(row["data"]))
            entry.estado = TaskQueueState.PENDING
            entry.leased_by = None
            entry.leased_until = None
            entry.updated_at = _ahora()
            self._update(entry)
            count += 1
        return count

    def count(self, estado: TaskQueueState | None = None) -> int:
        if estado is None:
            row = self._engine.query_one("SELECT COUNT(*) as c FROM task_queue")
        else:
            row = self._engine.query_one(
                "SELECT COUNT(*) as c FROM task_queue WHERE estado = ?",
                (estado.value,),
            )
        return row["c"] if row else 0

    def _update(self, entry: TaskEntry) -> None:
        data_json = json.dumps(entry.to_dict(), ensure_ascii=False, default=str)
        now = entry.updated_at.isoformat()
        self._engine.execute(
            "UPDATE task_queue SET data = ?, updated_at = ?, estado = ?, "
            "available_at = ?, leased_by = ?, leased_until = ? "
            "WHERE task_id = ?",
            (
                data_json,
                now,
                entry.estado.value,
                entry.available_at.isoformat(),
                entry.leased_by,
                entry.leased_until.isoformat() if entry.leased_until else None,
                entry.task_id,
            ),
        )
