"""
eon.memory.episodic
=====================
Memoria episódica: persiste ejecuciones completas como episodios.

Cada episodio captura: objetivo, plan, resultado, duración, coste,
tasks fallidas y replanificaciones. Permite buscar episodios similares
por similitud de descripción.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Episode:
    """Episodio: registro de una ejecución completa."""

    id: str = ""
    execution_id: str = ""
    objective_description: str = ""
    success_criteria: str = ""
    plan_summary: str = ""
    result_cumple: bool = False
    result_confidence: float = 0.0
    duration_seconds: float = 0.0
    cost_usd: float = 0.0
    failed_tasks: list[str] = field(default_factory=list)
    replans: int = 0
    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    created_at: str = field(default_factory=_ahora)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Episode:
        return cls(
            id=d.get("id", ""),
            execution_id=d.get("execution_id", ""),
            objective_description=d.get("objective_description", ""),
            success_criteria=d.get("success_criteria", ""),
            plan_summary=d.get("plan_summary", ""),
            result_cumple=d.get("result_cumple", False),
            result_confidence=d.get("result_confidence", 0.0),
            duration_seconds=d.get("duration_seconds", 0.0),
            cost_usd=d.get("cost_usd", 0.0),
            failed_tasks=list(d.get("failed_tasks", [])),
            replans=d.get("replans", 0),
            tasks_total=d.get("tasks_total", 0),
            tasks_completed=d.get("tasks_completed", 0),
            tasks_failed=d.get("tasks_failed", 0),
            created_at=d.get("created_at", _ahora()),
            metadata=dict(d.get("metadata", {})),
        )


class EpisodicMemoryStore:
    """Almacén durable de episodios con backend SQLite.

    Uso:
        store = EpisodicMemoryStore(db_path="/data/episodes.db")
        store.save(episode)
        similar = store.search_similar("generar informe de ventas")
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                execution_id TEXT,
                objective_description TEXT,
                success_criteria TEXT,
                plan_summary TEXT,
                result_cumple INTEGER,
                result_confidence REAL,
                duration_seconds REAL,
                cost_usd REAL,
                failed_tasks TEXT,
                replans INTEGER,
                tasks_total INTEGER,
                tasks_completed INTEGER,
                tasks_failed INTEGER,
                created_at TEXT,
                metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ep_desc ON episodes(objective_description);
            CREATE INDEX IF NOT EXISTS idx_ep_exec ON episodes(execution_id);
            CREATE INDEX IF NOT EXISTS idx_ep_cumple ON episodes(result_cumple);
            """
        )
        self._conn.commit()

    def save(self, episode: Episode) -> Episode:
        if not episode.id:
            import uuid
            episode.id = str(uuid.uuid4())

        self._conn.execute(
            """
            INSERT OR REPLACE INTO episodes
            (id, execution_id, objective_description, success_criteria, plan_summary,
             result_cumple, result_confidence, duration_seconds, cost_usd,
             failed_tasks, replans, tasks_total, tasks_completed, tasks_failed,
             created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode.id,
                episode.execution_id,
                episode.objective_description,
                episode.success_criteria,
                episode.plan_summary,
                int(episode.result_cumple),
                episode.result_confidence,
                episode.duration_seconds,
                episode.cost_usd,
                json.dumps(episode.failed_tasks),
                episode.replans,
                episode.tasks_total,
                episode.tasks_completed,
                episode.tasks_failed,
                episode.created_at,
                json.dumps(episode.metadata),
            ),
        )
        self._conn.commit()
        return episode

    def get(self, episode_id: str) -> Episode | None:
        row = self._conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        return self._row_to_episode(row) if row else None

    def list_all(self, limit: int = 100) -> list[Episode]:
        rows = self._conn.execute(
            "SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_episode(r) for r in rows]

    def list_by_execution(self, execution_id: str) -> list[Episode]:
        rows = self._conn.execute(
            "SELECT * FROM episodes WHERE execution_id = ? ORDER BY created_at DESC",
            (execution_id,),
        ).fetchall()
        return [self._row_to_episode(r) for r in rows]

    def search_similar(
        self,
        description: str,
        limit: int = 5,
        only_successful: bool = False,
    ) -> list[Episode]:
        """Busca episodios por similitud de descripción.

        Usa similitud de palabras (Jaccard) — no requiere embeddings.
        Para similitud semántica avanzada, usar SemanticMemory.
        """
        desc_words = set(description.lower().split())
        all_episodes = self.list_all(limit=500)

        if only_successful:
            all_episodes = [e for e in all_episodes if e.result_cumple]

        scored: list[tuple[float, Episode]] = []
        for ep in all_episodes:
            ep_words = set(ep.objective_description.lower().split())
            if not ep_words:
                continue
            # Coeficiente de Jaccard
            intersection = len(desc_words & ep_words)
            union = len(desc_words | ep_words)
            similarity = intersection / union if union > 0 else 0.0
            scored.append((similarity, ep))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:limit]]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_episode(row: sqlite3.Row) -> Episode:
        return Episode.from_dict({
            "id": row["id"],
            "execution_id": row["execution_id"],
            "objective_description": row["objective_description"],
            "success_criteria": row["success_criteria"],
            "plan_summary": row["plan_summary"],
            "result_cumple": bool(row["result_cumple"]),
            "result_confidence": row["result_confidence"],
            "duration_seconds": row["duration_seconds"],
            "cost_usd": row["cost_usd"],
            "failed_tasks": json.loads(row["failed_tasks"] or "[]"),
            "replans": row["replans"],
            "tasks_total": row["tasks_total"],
            "tasks_completed": row["tasks_completed"],
            "tasks_failed": row["tasks_failed"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata"] or "{}"),
        })
