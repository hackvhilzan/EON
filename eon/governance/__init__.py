"""eon.governance — Subsistema de Gobernanza (Fase 3).

La gobernanza es determinista y previa a la ejecución: el ``PolicyEngine``
evalúa cada Task **antes** de que llegue al ``Dispatcher``. Toda decisión
—permitida o denegada— se registra en el ``AuditLog`` y se publica en el
``EventBus`` compartido del kernel.

Componentes:
    - ``PolicyEngine``: decide si una Task puede ejecutarse (ALLOW, DENY,
      REQUIRES_APPROVAL).
    - ``CapabilityPolicy``: política declarativa que mapea capabilities a
      decisiones bajo condiciones.
    - ``SandboxProfile``: límites de ejecución (filesystem, red, tiempo,
      memoria).
    - ``AuditLog``: log append-only con cadena de hashes SHA-256.
    - ``AuditEntry``: entrada inmutable del AuditLog.
    - ``ExecutionContext``: contexto de ejecución pasado al PolicyEngine.
    - ``PolicyDecision``: enumeración de decisiones posibles.
    - ``PolicyResult``: resultado de evaluar una Task.

Eventos:
    - ``POLICY_EVALUATED``, ``POLICY_DENIED``, ``POLICY_APPROVED``
    - ``SANDBOX_APPLIED``, ``SANDBOX_VIOLATION``
    - ``AUDIT_RECORDED``
    - ``APPROVAL_REQUESTED``, ``APPROVAL_GRANTED``, ``APPROVAL_DENIED``
"""

from __future__ import annotations

from .audit_log import AuditLog
from .events import (
    APPROVAL_DENIED,
    APPROVAL_GRANTED,
    APPROVAL_REQUESTED,
    AUDIT_RECORDED,
    POLICY_APPROVED,
    POLICY_DENIED,
    POLICY_EVALUATED,
    SANDBOX_APPLIED,
    SANDBOX_VIOLATION,
    TODOS,
)
from .models import (
    AuditEntry,
    CapabilityPolicy,
    CapabilityPolicyProtocol,
    ExecutionContext,
    PolicyDecision,
    PolicyResult,
    SandboxProfile,
)
from .policy_engine import PolicyEngine

__all__ = [
    # Policy engine
    "PolicyEngine",
    "CapabilityPolicy",
    "CapabilityPolicyProtocol",
    "PolicyDecision",
    "PolicyResult",
    # Sandbox
    "SandboxProfile",
    # Context
    "ExecutionContext",
    # Audit
    "AuditLog",
    "AuditEntry",
    # Events
    "POLICY_EVALUATED",
    "POLICY_DENIED",
    "POLICY_APPROVED",
    "SANDBOX_APPLIED",
    "SANDBOX_VIOLATION",
    "AUDIT_RECORDED",
    "APPROVAL_REQUESTED",
    "APPROVAL_GRANTED",
    "APPROVAL_DENIED",
    "TODOS",
]
