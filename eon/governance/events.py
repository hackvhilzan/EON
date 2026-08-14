"""Catálogo de eventos publicados por el subsistema de Gobernanza (Fase 3).

La gobernanza es determinista y previa a la ejecución: el ``PolicyEngine``
evalúa cada Task **antes** de que llegue al ``Dispatcher``. Toda decisión
—permitida o denegada— se registra en el ``AuditLog`` y se publica en el
``EventBus`` compartido del kernel.
"""

from __future__ import annotations

POLICY_EVALUATED = "policy_evaluated"
POLICY_DENIED = "policy_denied"
POLICY_APPROVED = "policy_approved"
SANDBOX_APPLIED = "sandbox_applied"
SANDBOX_VIOLATION = "sandbox_violation"
AUDIT_RECORDED = "audit_recorded"
APPROVAL_REQUESTED = "approval_requested"
APPROVAL_GRANTED = "approval_granted"
APPROVAL_DENIED = "approval_denied"

TODOS = frozenset(
    {
        POLICY_EVALUATED,
        POLICY_DENIED,
        POLICY_APPROVED,
        SANDBOX_APPLIED,
        SANDBOX_VIOLATION,
        AUDIT_RECORDED,
        APPROVAL_REQUESTED,
        APPROVAL_GRANTED,
        APPROVAL_DENIED,
    }
)
