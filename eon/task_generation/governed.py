"""
eon.task_generation.governed
============================
Fase 4 — Planificación inteligente gobernada.

``GovernedTaskGenerator`` es un generador de TaskSpecs que encadena tres
capas de control **antes** de que el Planner reciba las tasks:

1. **Generador interno** (típicamente ``LLMTaskGenerator``): el LLM
   propone TaskSpecs a partir del objetivo natural.
2. **``TaskSpecValidator``**: valida schema, capabilities permitidas y
   integridad de dependencias.
3. **``PolicyEngine``**: preflight — evalúa cada TaskSpec contra las
   políticas activas de gobernanza. Si alguna es ``DENY`` o
   ``REQUIRES_APPROVAL``, el plan se rechaza antes de llegar al Planner.

Regla de Fase 4: «El LLM propone. EON valida, gobierna y ejecuta —
y gobierna dos veces: antes del Plan y antes del Worker.»

Este generador es la **primera** barrera de gobernanza. La **segunda**
es el gate de runtime en ``KernelRuntime._drain_dispatch_queue()``, que
evalúa cada Task individualmente justo antes de despacharla al Worker.
Entre ambas, el Plan nunca contiene tasks que el PolicyEngine no haya
pre-aprobado.
"""

from __future__ import annotations

import logging
from typing import Any

from ..governance import ExecutionContext, PolicyDecision, PolicyEngine
from .generator import TaskGenerator
from .models import TaskSpec
from .validator import TaskSpecValidator

logger = logging.getLogger("eon.task_generation.governed")


class GovernedTaskRejectionError(ValueError):
    """Una o más TaskSpecs fueron rechazadas por el PolicyEngine preflight.

    El LLM propuso una o más capabilities que el PolicyEngine denegó
    antes de que el Plan se cree. El rechazo es determinista: incluye
    qué spec fue rechazada, por qué política y con qué motivo.
    """

    def __init__(
        self,
        rejected: list[dict[str, Any]],
        reason: str = "TaskSpecs rechazadas por el PolicyEngine preflight",
    ) -> None:
        self.rejected = rejected
        self.reason = reason
        details = "; ".join(
            f"capability_id='{r['capability_id']}' policy='{r['policy_id']}' "
            f"decision='{r['decision']}' reason='{r['reason']}'"
            for r in rejected
        )
        super().__init__(f"{reason}: {details}")


class GovernedTaskGenerator(TaskGenerator):
    """Compone generador + validador + PolicyEngine en un solo
    ``TaskGenerator``.

    Flujo:
        objetivo → inner.generar() → validator.validar() →
        policy_engine.decidir(cada spec) → specs aprobadas

    Si cualquier spec es ``DENY`` o ``REQUIRES_APPROVAL``, se lanza
    ``GovernedTaskRejectionError`` y el Plan nunca se crea.

    Si el PolicyEngine es ``None``, se comporta como
    ``ValidatingTaskGenerator`` (solo validación de schema).
    """

    def __init__(
        self,
        inner: TaskGenerator,
        validator: TaskSpecValidator,
        policy_engine: PolicyEngine | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> None:
        self._inner = inner
        self._validator = validator
        self._policy_engine = policy_engine
        self._execution_context = execution_context or ExecutionContext()

    def generar(self, objetivo: Any) -> list[TaskSpec]:
        """Genera, valida y gobierna las TaskSpecs.

        1. El generador interno propone specs.
        2. El validador comprueba schema y dependencias.
        3. Si hay PolicyEngine, evalúa cada spec contra las políticas.
        4. Si todas pasan, devuelve las specs.
        5. Si alguna es denegada, lanza ``GovernedTaskRejectionError``.
        """
        # Paso 1: generar
        specs = self._inner.generar(objetivo)

        # Paso 2: validar schema y dependencias
        specs = self._validator.validar(specs)

        # Paso 3: preflight de gobernanza
        if self._policy_engine is not None:
            specs = self._policy_preflight(specs, objetivo)

        return specs

    def _policy_preflight(
        self,
        specs: list[TaskSpec],
        objetivo: Any,
    ) -> list[TaskSpec]:
        """Evalúa cada TaskSpec contra el PolicyEngine.

        Si alguna spec es ``DENY`` o ``REQUIRES_APPROVAL``, lanza
        ``GovernedTaskRejectionError`` con los detalles.
        """
        rejected: list[dict[str, Any]] = []
        approved: list[TaskSpec] = []

        for spec in specs:
            result = self._policy_engine.decidir(  # type: ignore[union-attr]
                spec, objective=objetivo, context=self._execution_context
            )
            if result.decision is PolicyDecision.ALLOW:
                approved.append(spec)
            else:
                rejected.append(
                    {
                        "task_id": spec.id,
                        "capability_id": spec.capability_id,
                        "decision": result.decision.value,
                        "policy_id": result.policy_id,
                        "reason": result.reason,
                    }
                )
                logger.warning(
                    "TaskSpec rechazada por preflight: capability_id='%s' policy='%s' decision='%s' reason='%s'",
                    spec.capability_id,
                    result.policy_id,
                    result.decision.value,
                    result.reason,
                )

        if rejected:
            raise GovernedTaskRejectionError(rejected=rejected)

        return approved
