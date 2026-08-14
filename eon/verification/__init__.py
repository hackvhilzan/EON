"""
eon.verification
=================
Verificación avanzada multi-capa.

Reemplaza el Verifier básico (que solo mira evidence["ok"]) con un
sistema de verificación multi-capa que evalúa calidad real del resultado.

Capas:
1. StructuralVerifier — todas las Tasks completadas, artefactos existen
2. CriteriaVerifier — parsea criterios de éxito medibles
3. LLMJudgeVerifier — un LLM evalúa el resultado contra el criterio
4. TestBasedVerifier — genera tests automáticos para el resultado
5. ExternalVerifier — hooks para validadores externos (linters, type checkers)

CompositeVerifier combina todas las capas y agrega confianzas.
EvidenceGraph conecta Task → ToolResult → artefacto → criterio verificado.
"""

from __future__ import annotations

from .composite_verifier import CompositeVerifier
from .confidence import ConfidenceCalibrator
from .criteria_verifier import CriteriaVerifier
from .external_verifier import ExternalVerifier
from .llm_judge_verifier import LLMJudgeVerifier
from .models import (
    EvidenceGraph,
    EvidenceNode,
    LayerResult,
    VerificationResult,
    VerificationStatus,
)
from .runtime_adapter import CompositeVerifierAdapter
from .structural_verifier import StructuralVerifier
from .testgen_verifier import TestBasedVerifier

__all__ = [
    "CompositeVerifier",
    "CompositeVerifierAdapter",
    "StructuralVerifier",
    "CriteriaVerifier",
    "LLMJudgeVerifier",
    "TestBasedVerifier",
    "ExternalVerifier",
    "ConfidenceCalibrator",
    "VerificationResult",
    "LayerResult",
    "VerificationStatus",
    "EvidenceNode",
    "EvidenceGraph",
]
