"""
eon.verification.composite_verifier
====================================
CompositeVerifier — combina múltiples capas de verificación.

Ejecuta todas las capas registradas, agrega confianzas con
ConfidenceCalibrator, y construye un EvidenceGraph.

Reglas:
- Las capas no aplicables (SKIPPED) no afectan el resultado.
- Una capa FAILED crítica puede fallar toda la verificación.
- La confianza final es un score calibrable, no una probabilidad real.
"""
from __future__ import annotations

import logging
from typing import Any

from .confidence import ConfidenceCalibrator
from .models import (
    EvidenceGraph,
    EvidenceNode,
    LayerResult,
    VerificationResult,
    VerificationStatus,
)

logger = logging.getLogger("eon.verification.composite")


class CompositeVerifier:
    """Verificador multi-capa que combina múltiples layers.

    Uso:
        verifier = CompositeVerifier()
        verifier.add_layer(StructuralVerifier())
        verifier.add_layer(CriteriaVerifier())
        result = verifier.verificar(objective, evidence, artifacts)
        if result.cumple:
            print(f"Verificado con confianza {result.confianza}")
    """

    def __init__(
        self,
        calibrator: ConfidenceCalibrator | None = None,
        fail_fast: bool = False,
    ) -> None:
        self._layers: list[Any] = []  # list of verifier objects with .verificar()
        self._calibrator = calibrator or ConfidenceCalibrator()
        self._fail_fast = fail_fast  # si True, primer FAIL detiene

    def add_layer(self, verifier: Any) -> None:
        """Añade una capa de verificación."""
        self._layers.append(verifier)

    @property
    def layers(self) -> list[Any]:
        return list(self._layers)

    @property
    def calibrator(self) -> ConfidenceCalibrator:
        return self._calibrator

    def verificar(
        self,
        objective: Any,
        evidence: dict[str, Any] | None = None,
        artifacts: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> VerificationResult:
        """Ejecuta todas las capas y agrega resultados.

        Args:
            objective: El objetivo a verificar (con criterio_de_exito).
            evidence: Evidence dict (tasks_totales, tasks_completadas, etc.).
            artifacts: Artefactos producidos por la ejecución.
            **kwargs: Argumentos adicionales para las capas.

        Returns:
            VerificationResult con la decisión agregada.
        """
        evidence = evidence or {}
        artifacts = artifacts or {}

        layer_results: list[LayerResult] = []

        for layer in self._layers:
            try:
                result = layer.verificar(
                    objective=objective,
                    evidence=evidence,
                    artifacts=artifacts,
                    **kwargs,
                )
                layer_results.append(result)

                if self._fail_fast and result.status == VerificationStatus.FAILED:
                    logger.info(
                        "Fail-fast: capa %s falló, deteniendo verificación",
                        result.layer_name,
                    )
                    break
            except Exception as exc:
                logger.exception("Error en capa %s", getattr(layer, "name", "unknown"))
                layer_results.append(
                    LayerResult(
                        layer_name=getattr(layer, "name", "unknown"),
                        status=VerificationStatus.ERROR,
                        motivo=str(exc),
                    )
                )

        # Calcular confianza calibrada
        confianza = self._calibrator.calibrate(layer_results)

        # Determinar si cumple:
        # - No puede haber ninguna capa FAILED crítica
        # - Al menos una capa debe haber PASSED
        any_passed = any(l.status == VerificationStatus.PASSED for l in layer_results)
        any_failed = any(
            l.status == VerificationStatus.FAILED and l.applicable
            for l in layer_results
        )
        any_error = any(l.status == VerificationStatus.ERROR for l in layer_results)

        cumple = any_passed and not any_failed

        # Si hay errores, la confianza baja
        if any_error:
            confianza *= 0.5

        # Motivo agregado
        motivos = [
            f"{l.layer_name}: {l.motivo}"
            for l in layer_results
            if l.applicable
        ]
        motivo = "; ".join(motivos) if motivos else "Sin capas aplicables"

        # Construir EvidenceGraph
        graph = self._build_evidence_graph(evidence, artifacts, layer_results)

        return VerificationResult(
            cumple=cumple,
            confianza=confianza,
            motivo=motivo,
            layers=layer_results,
            evidence_graph=graph.to_dict(),
        )

    @staticmethod
    def _build_evidence_graph(
        evidence: dict[str, Any],
        artifacts: dict[str, Any],
        layer_results: list[LayerResult],
    ) -> EvidenceGraph:
        """Construye un grafo de evidencias."""
        graph = EvidenceGraph()

        # Nodo raíz: evidence
        root = EvidenceNode(
            node_id="evidence",
            node_type="evidence",
            value=evidence,
        )
        graph.add_node(root)

        # Nodos de artefactos
        for name, value in artifacts.items():
            node_id = f"artifact:{name}"
            graph.add_node(EvidenceNode(
                node_id=node_id,
                node_type="artifact",
                value=value,
            ))
            graph.add_edge("evidence", node_id)

        # Nodos de layers de verificación
        for layer in layer_results:
            node_id = f"layer:{layer.layer_name}"
            graph.add_node(EvidenceNode(
                node_id=node_id,
                node_type="verification_layer",
                value={
                    "status": layer.status.value,
                    "confidence": layer.confidence,
                    "motivo": layer.motivo,
                },
            ))
            graph.add_edge("evidence", node_id)

        return graph
