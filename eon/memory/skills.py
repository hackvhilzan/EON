"""
eon.memory.skills
====================
Skill Library: planes exitosos reutilizables.

Un "skill" es un plan que tuvo éxito y se puede reutilizar.
Si un objetivo nuevo es similar a uno anterior exitoso, se sugiere
reutilizar el plan. Skills versionados: si el plan cambió, la skill
se actualiza.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Skill:
    """Skill: plan exitoso reutilizable."""

    id: str = ""
    name: str = ""
    objective_pattern: str = ""  # descripción del objetivo que resuelve
    plan_summary: str = ""  # resumen del plan
    plan_data: dict[str, Any] = field(default_factory=dict)  # plan serializado
    version: int = 1
    success_count: int = 0  # veces que se reutilizó con éxito
    fail_count: int = 0  # veces que falló al reutilizarse
    created_at: str = field(default_factory=_ahora)
    updated_at: str = field(default_factory=_ahora)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        return self.success_count / total if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Skill:
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            objective_pattern=d.get("objective_pattern", ""),
            plan_summary=d.get("plan_summary", ""),
            plan_data=dict(d.get("plan_data", {})),
            version=d.get("version", 1),
            success_count=d.get("success_count", 0),
            fail_count=d.get("fail_count", 0),
            created_at=d.get("created_at", _ahora()),
            updated_at=d.get("updated_at", _ahora()),
        )


class SkillLibrary:
    """Biblioteca de skills reutilizables.

    Uso:
        lib = SkillLibrary(db_path="/data/skills.db")
        lib.register(skill)
        match = lib.match("generar informe de ventas")
        if match:
            print(f"Skill sugerida: {match.name}")
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT,
                objective_pattern TEXT,
                plan_summary TEXT,
                plan_data TEXT,
                version INTEGER,
                success_count INTEGER,
                fail_count INTEGER,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sk_pattern ON skills(objective_pattern);
            """
        )
        self._conn.commit()

    def register(self, skill: Skill) -> Skill:
        if not skill.id:
            skill.id = str(uuid.uuid4())
        skill.updated_at = _ahora()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO skills
            (id, name, objective_pattern, plan_summary, plan_data, version,
             success_count, fail_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skill.id,
                skill.name,
                skill.objective_pattern,
                skill.plan_summary,
                json.dumps(skill.plan_data),
                skill.version,
                skill.success_count,
                skill.fail_count,
                skill.created_at,
                skill.updated_at,
            ),
        )
        self._conn.commit()
        return skill

    def get(self, skill_id: str) -> Skill | None:
        row = self._conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        return self._row_to_skill(row) if row else None

    def list_all(self) -> list[Skill]:
        rows = self._conn.execute("SELECT * FROM skills ORDER BY updated_at DESC").fetchall()
        return [self._row_to_skill(r) for r in rows]

    def match(self, objective: str, min_score: float = 0.1) -> Skill | None:
        """Busca la skill que mejor coincide con el objetivo.

        Usa similitud de palabras (Jaccard). Solo sugiere skills
        con success_count > 0 (al menos un éxito registrado).
        """
        obj_words = set(objective.lower().split())
        best_score = 0.0
        best_skill: Skill | None = None

        for skill in self.list_all():
            # Solo sugerir skills con al menos un éxito
            if skill.success_count == 0:
                continue
            pattern_words = set(skill.objective_pattern.lower().split())
            if not pattern_words:
                continue
            intersection = len(obj_words & pattern_words)
            union = len(obj_words | pattern_words)
            similarity = intersection / union if union > 0 else 0.0

            # Penalizar skills con baja tasa de éxito
            adjusted = similarity * (0.5 + 0.5 * skill.success_rate)

            if adjusted > best_score and adjusted >= min_score:
                best_score = adjusted
                best_skill = skill

        return best_skill

    def record_success(self, skill_id: str) -> None:
        skill = self.get(skill_id)
        if skill:
            skill.success_count += 1
            self.register(skill)

    def record_failure(self, skill_id: str) -> None:
        skill = self.get(skill_id)
        if skill:
            skill.fail_count += 1
            self.register(skill)

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM skills").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_skill(row: sqlite3.Row) -> Skill:
        return Skill.from_dict(
            {
                "id": row["id"],
                "name": row["name"],
                "objective_pattern": row["objective_pattern"],
                "plan_summary": row["plan_summary"],
                "plan_data": json.loads(row["plan_data"] or "{}"),
                "version": row["version"],
                "success_count": row["success_count"],
                "fail_count": row["fail_count"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
