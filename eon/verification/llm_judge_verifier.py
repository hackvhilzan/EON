"""
eon.verification.llm_judge_verifier
=====================================
Layer 3: LLMJudgeVerifier.

Un LLM evalúa el resultado contra el criterio de éxito.
Devuelve (cumple, confianza, justificacion).

El provider LLM es inyectable/mockeable — nunca se hacen llamadas
API reales en tests.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from .models import LayerResult, VerificationStatus

logger = logging.getLogger("eon.verification.llm_judge")


class JudgeProtocol(Protocol):
    """Protocol para un juez LLM."""

    def juzgar(self, criterio: str, resultado: str) -> tuple[bool, float, str]:
        """Juzga si el resultado cumple el criterio.

        Returns:
            (cumple, confianza, justificacion)
        """
        ...


class LLMJudgeVerifier:
    """Verifica resultados usando un LLM como juez.

    El LLM recibe: criterio de éxito + resultado/artefactos.
    Devuelve: (cumple, confianza, justificacion).

    El modelo del juez debe ser diferente al de ejecución para
    evitar sesgos compartidos.
    """

    name = "llm_judge"

    def __init__(self, judge: JudgeProtocol | None = None) -> None:
        self._judge = judge

    def verificar(
        self,
        objective: Any,
        evidence: dict[str, Any],
        artifacts: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LayerResult:
        if self._judge is None:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay juez LLM configurado",
            )

        criterio = ""
        if hasattr(objective, "criterio_de_exito"):
            criterio = objective.criterio_de_exito or ""
        elif isinstance(objective, str):
            criterio = objective
        elif isinstance(objective, dict):
            criterio = objective.get("criterio_de_exito", "")

        if not criterio:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay criterio_de_exito para juzgar",
            )

        # Preparar resultado para el juez
        result_str = self._format_result(evidence, artifacts or {})

        try:
            cumple, confianza, justificacion = self._judge.juzgar(criterio, result_str)
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.PASSED if cumple else VerificationStatus.FAILED,
                confidence=float(confianza),
                motivo=justificacion,
                details={"criterio": criterio[:200], "result_preview": result_str[:200]},
            )
        except Exception as exc:
            logger.exception("Error en LLM judge")
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.ERROR,
                motivo=f"Error del juez LLM: {exc}",
            )

    @staticmethod
    def _format_result(evidence: dict[str, Any], artifacts: dict[str, Any]) -> str:
        parts = [f"Evidence: {evidence}"]
        for name, value in artifacts.items():
            val_str = str(value)
            if len(val_str) > 500:
                val_str = val_str[:500] + "...[truncado]"
            parts.append(f"Artefacto '{name}': {val_str}")
        return "\n".join(parts)
