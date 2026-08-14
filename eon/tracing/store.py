"""
eon.tracing.store
==================
Persistencia SQLite para spans de tracing.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .models import Span

if TYPE_CHECKING:
    from eon.persistence.sqlite_engine import SQLiteEngine


class SQLiteTraceStore:
    """Store persistente para spans de tracing.

    Table: trace_spans
    - span_id TEXT PK
    - trace_id TEXT (indexed)
    - execution_id TEXT (indexed)
    - name TEXT
    - status TEXT
    - data TEXT (JSON completo del span)
    - started_at TEXT
    """

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._init_schema()

    def _init_schema(self) -> None:
        self._engine.executescript(
            """
            CREATE TABLE IF NOT EXISTS trace_spans (
                span_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                execution_id TEXT DEFAULT '',
                name TEXT DEFAULT '',
                status TEXT DEFAULT 'started',
                data TEXT NOT NULL,
                started_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trace_id ON trace_spans(trace_id);
            CREATE INDEX IF NOT EXISTS idx_trace_exec ON trace_spans(execution_id);
            """
        )

    def save_span(self, span: Span) -> None:
        data_json = json.dumps(span.to_dict(), ensure_ascii=False, default=str)
        self._engine.execute(
            """
            INSERT OR REPLACE INTO trace_spans
                (span_id, trace_id, execution_id, name, status, data, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                span.span_id,
                span.trace_id,
                span.execution_id,
                span.name,
                span.status.value,
                data_json,
                span.started_at,
            ),
        )

    def get_trace(self, trace_id: str) -> list[Span]:
        rows = self._engine.query_all(
            "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at",
            (trace_id,),
        )
        return [Span.from_dict(json.loads(r["data"])) for r in rows]

    def list_spans(self, execution_id: str) -> list[Span]:
        rows = self._engine.query_all(
            "SELECT * FROM trace_spans WHERE execution_id = ? ORDER BY started_at",
            (execution_id,),
        )
        return [Span.from_dict(json.loads(r["data"])) for r in rows]

    def count(self, execution_id: str | None = None) -> int:
        if execution_id:
            rows = self._engine.query_all(
                "SELECT COUNT(*) as cnt FROM trace_spans WHERE execution_id = ?",
                (execution_id,),
            )
        else:
            rows = self._engine.query_all(
                "SELECT COUNT(*) as cnt FROM trace_spans"
            )
        return rows[0]["cnt"] if rows else 0
