"""
eon.persistence.event_store
===============================
EventStore para event sourcing.

Cada evento del Kernel se persiste en una tabla append-only.
Permite reconstruir el estado de una ejecución tras un reinicio
de proceso (event sourcing).

El outbox pattern: el EventBus persistente escribe al EventStore
ANTES de notificar a los subscribers. Si el proceso muere tras
escribir el evento pero antes de notificar, al reiniciar se puede
replay el evento.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .sqlite_engine import SQLiteEngine


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class EventEntry:
    """Entrada inmutable del EventStore."""

    seq: int
    id: str
    execution_id: str | None
    event_type: str
    payload: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "id": self.id,
            "execution_id": self.execution_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


class EventStore:
    """Almacén append-only de eventos para event sourcing.

    Uso:
        event_store = EventStore(engine)
        event_store.append("execution_id", "event_type", {"key": "value"})
        events = event_store.get_events("execution_id")
    """

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine

    def append(
        self,
        execution_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> EventEntry:
        """Persiste un evento. Append-only: nunca se modifica ni borra."""
        event_id = str(uuid.uuid4())
        timestamp = _ahora()
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)

        row = self._engine.query_one(
            "INSERT INTO events (id, execution_id, event_type, payload, timestamp) "
            "VALUES (?, ?, ?, ?, ?) RETURNING seq",
            (event_id, execution_id, event_type, payload_json, timestamp),
        )
        seq = row["seq"] if row else 0
        return EventEntry(
            seq=seq,
            id=event_id,
            execution_id=execution_id,
            event_type=event_type,
            payload=payload,
            timestamp=timestamp,
        )

    def get_events(
        self,
        execution_id: str | None = None,
        event_type: str | None = None,
        after_seq: int = 0,
        limit: int = 10000,
    ) -> list[EventEntry]:
        """Lee eventos, opcionalmente filtrados por execution_id y/o event_type."""
        conditions = ["seq > ?"]
        params: list[Any] = [after_seq]

        if execution_id is not None:
            conditions.append("execution_id = ?")
            params.append(execution_id)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM events WHERE {where} ORDER BY seq ASC LIMIT ?"
        params.append(limit)

        rows = self._engine.query_all(sql, tuple(params))
        return [
            EventEntry(
                seq=r["seq"],
                id=r["id"],
                execution_id=r["execution_id"],
                event_type=r["event_type"],
                payload=json.loads(r["payload"]),
                timestamp=r["timestamp"],
            )
            for r in rows
        ]

    def get_all_events(self, after_seq: int = 0, limit: int = 100000) -> list[EventEntry]:
        """Lee todos los eventos después de un seq (para replay global)."""
        return self.get_events(after_seq=after_seq, limit=limit)

    def get_events_until(
        self,
        execution_id: str | None = None,
        from_seq: int = 0,
        to_seq: int | None = None,
        event_type: str | None = None,
        limit: int = 100000,
    ) -> list[EventEntry]:
        """Lee eventos en un rango de seq bounded [from_seq+1, to_seq].

        Usado por TimeMachine para reconstrucción con rango cerrado.
        Si to_seq es None, lee hasta el último evento.
        """
        conditions: list[str] = ["seq > ?"]
        params: list[Any] = [from_seq]
        if execution_id is not None:
            conditions.append("execution_id = ?")
            params.append(execution_id)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if to_seq is not None:
            conditions.append("seq <= ?")
            params.append(to_seq)
        where = " AND ".join(conditions)
        sql = f"SELECT * FROM events WHERE {where} ORDER BY seq ASC LIMIT ?"
        params.append(limit)
        rows = self._engine.query_all(sql, tuple(params))
        return [
            EventEntry(
                seq=r["seq"],
                id=r["id"],
                execution_id=r["execution_id"],
                event_type=r["event_type"],
                payload=json.loads(r["payload"]),
                timestamp=r["timestamp"],
            )
            for r in rows
        ]

    def get_last_seq(self) -> int:
        """Devuelve el último sequence number, o 0 si no hay eventos."""
        row = self._engine.query_one("SELECT MAX(seq) as max_seq FROM events")
        return row["max_seq"] if row and row["max_seq"] else 0

    def count(self) -> int:
        """Número total de eventos."""
        row = self._engine.query_one("SELECT COUNT(*) as count FROM events")
        return row["count"] if row else 0
