"""
eon.verification.confidence
============================
Calibración de confianza multi-factor.

La confianza no es "proporción de Tasks completadas".
Es un score calibrable basado en cuántas layers de verificación pasaron,
la confianza de cada layer, y pesos configurables.

Pesos por defecto:
  structural: 0.15  — básico pero necesario
  criteria:   0.25  — criterios medibles
  llm_judge:  0.30  — juicio LLM (pesado si está disponible)
  test_based: 0.20  — tests automáticos
  external:   0.10  — validadores externos

Los pesos se pueden ajustar con aprendizaje online (Fase 9).
"""
from __future__ import annotations

from typing import Any

from .models import LayerResult, VerificationStatus


class ConfidenceCalibrator:
    """Calcula confianza calibrada a partir de resultados de layers.

    Uso:
        calibrator = ConfidenceCalibrator()
        confianza = calibrator.calibrate(layer_results)
    """

    DEFAULT_WEIGHTS: dict[str, float] = {
        "structural": 0.15,
        "criteria": 0.25,
        "llm_judge": 0.30,
        "test_based": 0.20,
        "external": 0.10,
    }

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._weights = dict(weights or self.DEFAULT_WEIGHTS)
        # Normalizar pesos para que sumen 1.0
        total = sum(self._weights.values())
        if total > 0:
            for k in self._weights:
                self._weights[k] /= total

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def update_weights(self, new_weights: dict[str, float]) -> None:
        """Actualiza los pesos (normaliza automáticamente)."""
        total = sum(new_weights.values())
        if total > 0:
            self._weights = {k: v / total for k, v in new_weights.items()}
        else:
            self._weights = dict(new_weights)

    def calibrate(self, layers: list[LayerResult]) -> float:
        """Calcula la confianza calibrada.

        - Solo las layers aplicables (no SKIPPED) contribuyen.
        - Si una layer aplicable falló, la confianza baja.
        - Los pesos se renormalizan sobre las layers aplicables.
        """
        applicable = [l for l in layers if l.applicable]
        if not applicable:
            return 0.0

        # Renormalizar pesos sobre layers aplicables
        active_weight = sum(
            self._weights.get(l.layer_name, 0.0) for l in applicable
        )
        if active_weight == 0:
            # Sin pesos definidos: promedio simple
            return sum(l.confidence for l in applicable) / len(applicable)

        score = 0.0
        for layer in applicable:
            weight = self._weights.get(layer.layer_name, 0.0) / active_weight
            if layer.status == VerificationStatus.PASSED:
                score += weight * layer.confidence
            elif layer.status == VerificationStatus.FAILED:
                score += weight * layer.confidence * 0.1  # penalización
            elif layer.status == VerificationStatus.ERROR:
                score += weight * 0.0  # error = sin contribución

        return min(1.0, max(0.0, score))

    def to_dict(self) -> dict[str, Any]:
        return {"weights": dict(self._weights)}
