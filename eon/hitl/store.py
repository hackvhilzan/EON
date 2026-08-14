"""
eon.hitl.store
===============
Persistencia SQLite para interrupciones HITL.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .models import HITLInterrupt

if TYPE_CHECKING:
    from eon.persistence.sqlite_engine import SQLiteEngine


class SQLiteHITLStore:
    """Store persistente para interrupciones HITL.

    Table: hitl_interrupts
    - id TEXT PK
    - execution_id TEXT
    - status TEXT
    - data TEXT (JSON completo)
    - created_at TEXT
    - resolved_at TEXT
    """

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._init_schema()

    def _init_schema(self) -> None:
        self._engine.executescript(
            """
            CREATE TABLE IF NOT EXISTS hitl_interrupts (
                id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_hitl_exec ON hitl_interrupts(execution_id);
            CREATE INDEX IF NOT EXISTS idx_hitl_status ON hitl_interrupts(status);
            """
        )

    def save(self, interrupt: HITLInterrupt) -> None:
        data_json = json.dumps(interrupt.to_dict(), ensure_ascii=False, default=str)
        self._engine.execute(
            """
            INSERT OR REPLACE INTO hitl_interrupts
                (id, execution_id, status, data, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                interrupt.id,
                interrupt.execution_id,
                interrupt.status.value,
                data_json,
                interrupt.created_at,
                interrupt.resolved_at,
            ),
        )

    def get(self, interrupt_id: str) -> HITLInterrupt | None:
        rows = self._engine.query_all("SELECT * FROM hitl_interrupts WHERE id = ?", (interrupt_id,))
        if not rows:
            return None
        row = rows[0]
        data = json.loads(row["data"])
        return HITLInterrupt.from_dict(data)

    def list_pending(self) -> list[HITLInterrupt]:
        rows = self._engine.query_all("SELECT * FROM hitl_interrupts WHERE status = 'pending' ORDER BY created_at")
        return [HITLInterrupt.from_dict(json.loads(r["data"])) for r in rows]

    def list_for_execution(self, execution_id: str) -> list[HITLInterrupt]:
        rows = self._engine.query_all(
            "SELECT * FROM hitl_interrupts WHERE execution_id = ? ORDER BY created_at",
            (execution_id,),
        )
        return [HITLInterrupt.from_dict(json.loads(r["data"])) for r in rows]

    def list_all(self) -> list[HITLInterrupt]:
        rows = self._engine.query_all("SELECT * FROM hitl_interrupts ORDER BY created_at DESC")
        return [HITLInterrupt.from_dict(json.loads(r["data"])) for r in rows]

    def count(self, status: str | None = None) -> int:
        if status:
            rows = self._engine.query_all(
                "SELECT COUNT(*) as cnt FROM hitl_interrupts WHERE status = ?",
                (status,),
            )
        else:
            rows = self._engine.query_all("SELECT COUNT(*) as cnt FROM hitl_interrupts")
        return rows[0]["cnt"] if rows else 0

    def update(self, interrupt: HITLInterrupt) -> None:
        self.save(interrupt)
