"""PolicyEngine: la única autoridad que decide si una Task puede ejecutarse.

El PolicyEngine evalúa cada Task contra las políticas activas **antes** de
que llegue al ``Dispatcher``. Es la última compuerta de gobernanza antes de
la ejecución.

Principios:
1. **Determinista antes de ejecutar.** La decisión se toma antes de que el
   Worker ejecute, no después.
2. **Denegación por defecto.** Si no hay una política explícita que permita
   una capability, se deniega (default-deny).
3. **Primera política que matcha gana.** Las políticas se evalúan en orden;
   la primera que aplica retorna su resultado.
"""

from __future__ import annotations

from typing import Any

from .models import (
    CapabilityPolicy,
    CapabilityPolicyProtocol,
    ExecutionContext,
    PolicyDecision,
    PolicyResult,
)


class PolicyEngine:
    """Evalúa cada Task contra las políticas activas antes de que llegue al
    Dispatcher. Es la única autoridad que decide si una Task puede
    ejecutarse."""

    def __init__(
        self,
        policies: list[CapabilityPolicy | CapabilityPolicyProtocol] | None = None,
        default: PolicyDecision = PolicyDecision.DENY,
    ) -> None:
        self._policies: list[CapabilityPolicy | CapabilityPolicyProtocol] = list(policies) if policies else []
        self._default = default

    def decidir(
        self,
        task: Any,
        objective: Any | None = None,
        context: ExecutionContext | None = None,
    ) -> PolicyResult:
        """Evalúa las políticas en orden. La primera que matcha gana.
        Si ninguna matcha, aplica el default (DENY)."""
        ctx = context or ExecutionContext()
        for policy in self._policies:
            result = policy.evaluar(task, objective, ctx)
            if result is not None:
                return result
        return PolicyResult(
            decision=self._default,
            reason="ninguna política permite esta capability",
            policy_id="default-deny",
        )

    def agregar(self, policy: CapabilityPolicy | CapabilityPolicyProtocol) -> None:
        """Añade una política al final de la cadena de evaluación."""
        self._policies.append(policy)

    @property
    def policies(self) -> list[CapabilityPolicy | CapabilityPolicyProtocol]:
        return list(self._policies)
