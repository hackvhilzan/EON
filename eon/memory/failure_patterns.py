"""
eon.memory.failure_patterns
=============================
Análisis de fallos: categoriza y aprende de los fallos.

Cada fallo se categoriza: Task fallida, capability no disponible,
timeout, rechazo del Verifier. Detecta patrones como "la Tool X
falla el 30% de las veces con inputs de tipo Y".
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class FailureCategory(str, Enum):
    """Categorías de fallo."""

    TASK_FAILED = "task_failed"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    TIMEOUT = "timeout"
    VERIFIER_REJECTION = "verifier_rejection"
    POLICY_DENIED = "policy_denied"
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    UNKNOWN = "unknown"


@dataclass
class FailureRecord:
    """Registro de un fallo individual."""

    id: str = ""
    execution_id: str = ""
    task_id: str = ""
    capability_id: str = ""
    category: str = FailureCategory.UNKNOWN.value
    error_message: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_ahora)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FailurePatterns:
    """Detecta y almacena patrones de fallo.

    Uso:
        fp = FailurePatterns(db_path="/data/failures.db")
        fp.record(failure)
        patterns = fp.get_patterns("tool.python")
        stats = fp.get_stats()
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS failures (
                id TEXT PRIMARY KEY,
                execution_id TEXT,
                task_id TEXT,
                capability_id TEXT,
                category TEXT,
                error_message TEXT,
                context TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fail_cap ON failures(capability_id);
            CREATE INDEX IF NOT EXISTS idx_fail_cat ON failures(category);
            CREATE INDEX IF NOT EXISTS idx_fail_exec ON failures(execution_id);
            """
        )
        self._conn.commit()

    def record(self, failure: FailureRecord) -> FailureRecord:
        if not failure.id:
            failure.id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO failures
            (id, execution_id, task_id, capability_id, category, error_message, context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                failure.id,
                failure.execution_id,
                failure.task_id,
                failure.capability_id,
                failure.category,
                failure.error_message,
                json.dumps(failure.context),
                failure.created_at,
            ),
        )
        self._conn.commit()
        return failure

    def get_patterns(self, capability_id: str) -> dict[str, Any]:
        """Obtiene patrones de fallo para una capability específica."""
        rows = self._conn.execute(
            "SELECT * FROM failures WHERE capability_id = ? ORDER BY created_at DESC",
            (capability_id,),
        ).fetchall()

        if not rows:
            return {"capability_id": capability_id, "total_failures": 0}

        total = len(rows)
        by_category: dict[str, int] = defaultdict(int)
        common_errors: dict[str, int] = defaultdict(int)

        for row in rows:
            by_category[row["category"]] += 1
            # Agrupar errores por mensaje simplificado
            err = row["error_message"][:100] if row["error_message"] else ""
            if err:
                common_errors[err] += 1

        return {
            "capability_id": capability_id,
            "total_failures": total,
            "by_category": dict(by_category),
            "most_common_error": max(common_errors, key=common_errors.get) if common_errors else None,
            "common_errors": dict(sorted(common_errors.items(), key=lambda x: x[1], reverse=True)[:5]),
        }

    def get_stats(self) -> dict[str, Any]:
        """Estadísticas globales de fallos."""
        total = self._conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        if total == 0:
            return {"total": 0, "by_category": {}, "by_capability": {}}

        # Por categoría
        cat_rows = self._conn.execute(
            "SELECT category, COUNT(*) as cnt FROM failures GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        by_category = {r["category"]: r["cnt"] for r in cat_rows}

        # Por capability
        cap_rows = self._conn.execute(
            "SELECT capability_id, COUNT(*) as cnt FROM failures GROUP BY capability_id ORDER BY cnt DESC"
        ).fetchall()
        by_capability = {r["capability_id"]: r["cnt"] for r in cap_rows}

        return {
            "total": total,
            "by_category": by_category,
            "by_capability": by_capability,
        }

    def get_failure_rate(self, capability_id: str, total_calls: int) -> float:
        """Calcula la tasa de fallo de una capability."""
        if total_calls == 0:
            return 0.0
        failures = self._conn.execute(
            "SELECT COUNT(*) FROM failures WHERE capability_id = ?",
            (capability_id,),
        ).fetchone()[0]
        return failures / total_calls

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]

    def close(self) -> None:
        self._conn.close()
