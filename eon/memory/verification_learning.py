"""
eon.memory.verification_learning
==================================
Aprendizaje de pesos de verificación.

Los pesos de confianza del CompositeVerifier se ajustan con el tiempo.
Si el LLMJudge tiene razón el 90% de las veces pero el TestBasedVerifier
el 70%, el peso del LLMJudge sube.

Aprendizaje online con decay: los datos viejos pesan menos.
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
class VerificationOutcome:
    """Registro de una verificación y su resultado real."""

    id: str = ""
    execution_id: str = ""
    layer_name: str = ""
    predicted_confidence: float = 0.0
    actual_correct: bool = False    # ¿La verificación de esta layer fue correcta?
    created_at: str = field(default_factory=_ahora)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VerificationWeightLearner:
    """Ajusta pesos de verificación basado en outcomes históricos.

    Usa decay exponencial: los outcomes recientes pesan más que
    los viejos. El factor de decay es configurable (default: 0.95).

    Uso:
        learner = VerificationWeightLearner(db_path="/data/vw.db")
        learner.record_outcome("exec-1", "llm_judge", 0.9, True)
        learner.record_outcome("exec-1", "test_based", 0.7, False)

        weights = learner.compute_weights()
        # weights = {"llm_judge": 0.6, "test_based": 0.4, ...}
    """

    DEFAULT_WEIGHTS: dict[str, float] = {
        "structural": 0.15,
        "criteria": 0.25,
        "llm_judge": 0.30,
        "test_based": 0.20,
        "external": 0.10,
    }

    def __init__(
        self,
        db_path: str = ":memory:",
        decay: float = 0.95,
        initial_weights: dict[str, float] | None = None,
    ) -> None:
        self._db_path = db_path
        self._decay = decay
        self._initial_weights = dict(initial_weights or self.DEFAULT_WEIGHTS)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS verification_outcomes (
                id TEXT PRIMARY KEY,
                execution_id TEXT,
                layer_name TEXT,
                predicted_confidence REAL,
                actual_correct INTEGER,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_vo_layer ON verification_outcomes(layer_name);
            CREATE INDEX IF NOT EXISTS idx_vo_exec ON verification_outcomes(execution_id);
            """
        )
        self._conn.commit()

    def record_outcome(
        self,
        execution_id: str,
        layer_name: str,
        predicted_confidence: float,
        actual_correct: bool,
    ) -> VerificationOutcome:
        """Registra el resultado real de una verificación."""
        outcome = VerificationOutcome(
            id=str(uuid.uuid4()),
            execution_id=execution_id,
            layer_name=layer_name,
            predicted_confidence=predicted_confidence,
            actual_correct=actual_correct,
        )
        self._conn.execute(
            """
            INSERT INTO verification_outcomes
            (id, execution_id, layer_name, predicted_confidence, actual_correct, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.id, outcome.execution_id, outcome.layer_name,
                outcome.predicted_confidence, int(outcome.actual_correct),
                outcome.created_at,
            ),
        )
        self._conn.commit()
        return outcome

    def compute_weights(self) -> dict[str, float]:
        """Calcula pesos basados en accuracy histórica con decay.

        Layers con mayor accuracy reciben más peso.
        El decay hace que outcomes viejos pesen menos.
        """
        # Obtener accuracy por layer con decay
        layer_scores: dict[str, float] = {}
        layer_weight_totals: dict[str, float] = {}

        for layer_name in self._initial_weights:
            rows = self._conn.execute(
                "SELECT * FROM verification_outcomes WHERE layer_name = ? ORDER BY created_at DESC",
                (layer_name,),
            ).fetchall()

            if not rows:
                layer_scores[layer_name] = self._initial_weights[layer_name]
                layer_weight_totals[layer_name] = 1.0
                continue

            # Calcular accuracy con decay
            decay_factor = 1.0
            total_score = 0.0
            total_weight = 0.0

            for row in rows:
                correct = bool(row["actual_correct"])
                score = 1.0 if correct else 0.0
                total_score += score * decay_factor
                total_weight += decay_factor
                decay_factor *= self._decay

            accuracy = total_score / total_weight if total_weight > 0 else 0.5
            layer_scores[layer_name] = accuracy
            layer_weight_totals[layer_name] = total_weight

        # Combinar accuracy con pesos iniciales (Bayesian-like)
        raw_weights: dict[str, float] = {}
        for layer_name, initial in self._initial_weights.items():
            accuracy = layer_scores.get(layer_name, 0.5)
            weight = total_weight = layer_weight_totals.get(layer_name, 0.0)
            # Prior strength: cuantos más datos, menos importa el prior
            prior_strength = 5.0  # equivale a 5 observaciones
            posterior = (accuracy * weight + initial * prior_strength) / (weight + prior_strength)
            raw_weights[layer_name] = posterior

        # Normalizar
        total = sum(raw_weights.values())
        if total > 0:
            return {k: v / total for k, v in raw_weights.items()}
        return dict(self._initial_weights)

    def get_accuracy(self, layer_name: str) -> float:
        """Obtiene la accuracy sin decay de una layer."""
        rows = self._conn.execute(
            "SELECT actual_correct FROM verification_outcomes WHERE layer_name = ?",
            (layer_name,),
        ).fetchall()
        if not rows:
            return 0.5
        correct = sum(1 for r in rows if r["actual_correct"])
        return correct / len(rows)

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM verification_outcomes").fetchone()[0]

    def close(self) -> None:
        self._conn.close()
