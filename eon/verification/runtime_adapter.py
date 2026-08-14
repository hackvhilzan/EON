"""
eon.verification.runtime_adapter
================================
Adapter that connects the CompositeVerifier (Fase 8) with the
Coordinator's VerifierPort contract.

The Coordinator expects ``VerifierPort.verificar(evidence, criterio, umbral)
-> VerificationResultRef`` (with ``dictamen``, ``confianza``,
``justificacion``). The CompositeVerifier has a richer interface:
``verificar(objective, evidence, artifacts) -> VerificationResult`` (with
``cumple``, ``confianza``, ``motivo``, ``layers``, ``evidence_graph``).

This adapter bridges the two:
- Translates ``evidence`` (from Scheduler) + ``criterio``/``umbral``
  into the CompositeVerifier's call.
- Translates the CompositeVerifier's ``VerificationResult`` back to
  ``VerifierPortResultado`` (which satisfies ``VerificationResultRef``).
- Stores ``last_result`` for metrics, memory and replanning to consume.

Design:
- Preserves the legacy boolean contract: ``dictamen`` is "aprobado" if
  ``cumple`` and ``confianza >= umbral``, else "rechazado".
- Does NOT change the Coordinator or its state machine.
- If CompositeVerifier is not configured, delegates to the original
  VerifierPortAdapter (backwards compatible).
"""

from __future__ import annotations

from typing import Any

from ..objectives.verifier import Verifier
from ..runtime import VerifierPortResultado


class CompositeVerifierAdapter:
    """Adapts CompositeVerifier to the VerifierPort contract.

    Can be used as a drop-in replacement for ``VerifierPortAdapter``
    in ``KernelRuntime.__init__``.

    Args:
        composite: A ``CompositeVerifier`` instance. If None, falls back
            to the basic ``Verifier`` (backwards compatible).
        basic_verifier: Fallback verifier when composite is None.
    """

    def __init__(
        self,
        composite: Any | None = None,
        basic_verifier: Verifier | None = None,
    ) -> None:
        self._composite = composite
        self._basic = basic_verifier or Verifier()
        self.last_result: Any | None = None
        self.last_dictamen: str = ""
        self.last_confidence: float = 0.0

    def verificar(
        self,
        evidence: Any,
        criterio: str,
        umbral: float,
    ) -> VerifierPortResultado:
        """Verify evidence against criteria.

        Implements ``VerifierPort.verificar`` contract.
        """
        if self._composite is not None:
            return self._verify_with_composite(evidence, criterio, umbral)
        return self._verify_with_basic(evidence, criterio, umbral)

    def _verify_with_composite(
        self,
        evidence: Any,
        criterio: str,
        umbral: float,
    ) -> VerifierPortResultado:
        """Use the CompositeVerifier for rich multi-layer verification."""
        # Build a lightweight objective-like object for CompositeVerifier
        objective = _ObjectiveProxy(
            criterio_de_exito=criterio,
            confianza_minima=umbral,
        )

        # Normalize evidence to dict for CompositeVerifier
        ev_dict = evidence if isinstance(evidence, dict) else {"value": evidence}

        result = self._composite.verificar(
            objective=objective,
            evidence=ev_dict,
        )

        # Store last result for metrics/memory/replan
        self.last_result = result
        self.last_confidence = result.confianza

        # Translate to VerifierPortResultado
        if result.cumple and result.confianza >= umbral:
            dictamen = "aprobado"
        else:
            dictamen = "rechazado"

        self.last_dictamen = dictamen

        return VerifierPortResultado(
            dictamen=dictamen,
            confianza=result.confianza,
            justificacion=result.motivo or f"CompositeVerifier: {dictamen}",
        )

    def _verify_with_basic(
        self,
        evidence: Any,
        criterio: str,
        umbral: float,
    ) -> VerifierPortResultado:
        """Fallback to the basic Verifier (same as VerifierPortAdapter)."""
        from ..runtime import VerifierPortAdapter

        adapter = VerifierPortAdapter(self._basic)
        result = adapter.verificar(evidence, criterio, umbral)
        self.last_result = result
        self.last_confidence = result.confianza
        self.last_dictamen = result.dictamen
        return result


class _ObjectiveProxy:
    """Minimal objective-like object for CompositeVerifier.

    CompositeVerifier expects an object with ``criterio_de_exito`` and
    ``confianza_minima``. This proxy avoids importing the real Objective
    model.
    """

    def __init__(
        self,
        criterio_de_exito: str,
        confianza_minima: float = 0.7,
    ) -> None:
        self.criterio_de_exito = criterio_de_exito
        self.confianza_minima = confianza_minima
