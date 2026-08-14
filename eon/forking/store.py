"""
eon.forking.store
===================
SQLiteForkStore — persistencia durable de forks de ejecución.

Tabla: execution_forks
  - id TEXT PRIMARY KEY
  - parent_execution_id TEXT (indexado)
  - new_execution_id TEXT (indexado)
  - data TEXT (JSON completo)
"""

from __future__ import annotations

import json

from ..persistence.sqlite_engine import SQLiteEngine
from .models import ExecutionFork


class SQLiteForkStore:
    """Almacén durable de forks de ejecución con backend SQLite."""

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._init_schema()

    def _init_schema(self) -> None:
        self._engine.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_forks (
                id TEXT PRIMARY KEY,
                parent_execution_id TEXT NOT NULL,
                new_execution_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                forked_at_seq INTEGER,
                status TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_fork_parent ON execution_forks(parent_execution_id);
            CREATE INDEX IF NOT EXISTS idx_fork_new ON execution_forks(new_execution_id);
            """
        )

    def save(self, fork: ExecutionFork) -> ExecutionFork:
        """Persiste un fork. Idempotente por id (INSERT OR REPLACE)."""
        data_json = json.dumps(fork.to_dict(), ensure_ascii=False, default=str)
        self._engine.execute(
            "INSERT OR REPLACE INTO execution_forks "
            "(id, parent_execution_id, new_execution_id, parent_checkpoint_id, "
            "forked_at_seq, status, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fork.id,
                fork.parent_execution_id,
                fork.new_execution_id,
                fork.parent_checkpoint_id,
                fork.forked_at_seq,
                fork.status.value,
                data_json,
                fork.created_at,
            ),
        )
        return fork

    def get(self, fork_id: str) -> ExecutionFork | None:
        row = self._engine.query_one("SELECT data FROM execution_forks WHERE id = ?", (fork_id,))
        if row is None:
            return None
        return ExecutionFork.from_dict(json.loads(row["data"]))

    def list_for_parent(self, parent_execution_id: str) -> list[ExecutionFork]:
        """Lista todos los forks de una ejecución parent."""
        rows = self._engine.query_all(
            "SELECT data FROM execution_forks WHERE parent_execution_id = ? ORDER BY created_at ASC",
            (parent_execution_id,),
        )
        return [ExecutionFork.from_dict(json.loads(r["data"])) for r in rows]

    def list_for_new_execution(self, new_execution_id: str) -> ExecutionFork | None:
        """Busca el fork que creó una ejecución dada."""
        row = self._engine.query_one(
            "SELECT data FROM execution_forks WHERE new_execution_id = ? LIMIT 1",
            (new_execution_id,),
        )
        if row is None:
            return None
        return ExecutionFork.from_dict(json.loads(row["data"]))

    def list_all(self) -> list[ExecutionFork]:
        """Lista todos los forks."""
        rows = self._engine.query_all("SELECT data FROM execution_forks ORDER BY created_at DESC")
        return [ExecutionFork.from_dict(json.loads(r["data"])) for r in rows]

    def update_status(self, fork_id: str, status: str, completed_at: str | None = None) -> bool:
        """Actualiza el estado de un fork."""
        fork = self.get(fork_id)
        if fork is None:
            return False
        fork.status = type(fork.status)(status)
        if completed_at:
            fork.completed_at = completed_at
        self.save(fork)
        return True

    def count(self, parent_execution_id: str | None = None) -> int:
        if parent_execution_id:
            row = self._engine.query_one(
                "SELECT COUNT(*) as c FROM execution_forks WHERE parent_execution_id = ?",
                (parent_execution_id,),
            )
        else:
            row = self._engine.query_one("SELECT COUNT(*) as c FROM execution_forks")
        return row["c"] if row else 0
