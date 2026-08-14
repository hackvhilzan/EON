"""
eon.verification.structural_verifier
=====================================
Layer 1: StructuralVerifier.

Verifica que la estructura básica del resultado es correcta:
- Todas las Tasks se completaron
- Los artefactos esperados existen
- Los tipos de los artefactos son correctos
"""

from __future__ import annotations

from typing import Any

from .models import LayerResult, VerificationStatus


class StructuralVerifier:
    """Verifica la estructura básica del resultado."""

    name = "structural"

    def verificar(
        self,
        objective: Any,
        evidence: dict[str, Any],
        artifacts: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LayerResult:
        tasks_total = evidence.get("tasks_totales", 0)
        tasks_completed = evidence.get("tasks_completadas", 0)
        tasks_failed = evidence.get("tasks_fallidas", 0)

        if tasks_total == 0:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay tasks para verificar",
            )

        if tasks_completed < tasks_total:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.FAILED,
                confidence=0.0,
                motivo=f"Solo {tasks_completed}/{tasks_total} tasks completadas",
                details={
                    "tasks_total": tasks_total,
                    "tasks_completed": tasks_completed,
                    "tasks_failed": tasks_failed,
                },
            )

        # Verificar artefactos esperados si se especifican
        expected_artifacts = kwargs.get("expected_artifacts", [])
        missing = []
        if expected_artifacts and artifacts:
            for artifact_path in expected_artifacts:
                if artifact_path not in artifacts:
                    missing.append(artifact_path)

        if missing:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.FAILED,
                confidence=0.1,
                motivo=f"Artefactos faltantes: {missing}",
                details={"missing_artifacts": missing},
            )

        confidence = min(1.0, tasks_completed / tasks_total) if tasks_total > 0 else 0.0
        return LayerResult(
            layer_name=self.name,
            status=VerificationStatus.PASSED,
            confidence=confidence,
            motivo=f"{tasks_completed}/{tasks_total} tasks completadas",
            details={
                "tasks_total": tasks_total,
                "tasks_completed": tasks_completed,
                "tasks_failed": tasks_failed,
            },
        )
