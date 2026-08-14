"""Modelos del subsistema de Gobernanza (Fase 3).

Define los tipos inmutables que usa el ``PolicyEngine``:

- ``PolicyDecision``: enumeración de decisiones posibles (ALLOW, DENY,
  REQUIRES_APPROVAL).
- ``PolicyResult``: resultado de evaluar una Task contra las políticas
  activas.
- ``CapabilityPolicy``: política declarativa que permite o deniega
  capabilities bajo condiciones específicas.
- ``SandboxProfile``: límites de ejecución (filesystem, red, tiempo,
  memoria) que se aplican al Workspace antes de que el Worker ejecute.
- ``ExecutionContext``: contexto de ejecución pasado al PolicyEngine.
- ``AuditEntry``: entrada inmutable del AuditLog con cadena de hashes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

# ─── PolicyDecision ───────────────────────────────────────────────


class PolicyDecision(str, Enum):
    """Tres decisiones posibles para una Task evaluada por el PolicyEngine."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"


# ─── ExecutionContext ─────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionContext:
    """Contexto de ejecución pasado al PolicyEngine. Contiene información
    sobre el usuario, el entorno de red y metadatos de la ejecución."""

    usuario: str | None = None
    red: bool = False
    dominios_permitidos: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── SandboxProfile ──────────────────────────────────────────────


@dataclass(frozen=True)
class SandboxProfile:
    """Define los límites de ejecución para una Task. Se aplica al Workspace
    antes de que el Worker ejecute."""

    profile_id: str
    filesystem_writable: bool = True
    filesystem_readable_paths: list[Path] | None = None
    network_allowed: bool = False
    network_allowlist: list[str] | None = None
    max_duration_seconds: float = 30.0
    max_memory_mb: int | None = None
    env_vars_allowed: list[str] | None = None

    @staticmethod
    def workspace_only() -> SandboxProfile:
        """Perfil más restrictivo: solo lee/escribe dentro del Workspace,
        sin red, 30s de timeout."""
        return SandboxProfile(
            profile_id="workspace-only",
            filesystem_writable=True,
            network_allowed=False,
            max_duration_seconds=30.0,
        )

    @staticmethod
    def read_only() -> SandboxProfile:
        """Solo lectura dentro del Workspace, sin red."""
        return SandboxProfile(
            profile_id="read-only",
            filesystem_writable=False,
            network_allowed=False,
            max_duration_seconds=15.0,
        )

    @staticmethod
    def network_restricted(
        allowlist: list[str] | None = None,
    ) -> SandboxProfile:
        """Workspace + red restringida a dominios en allowlist."""
        return SandboxProfile(
            profile_id="network-restricted",
            filesystem_writable=True,
            network_allowed=True,
            network_allowlist=allowlist,
            max_duration_seconds=60.0,
        )


# ─── PolicyResult ────────────────────────────────────────────────


@dataclass(frozen=True)
class PolicyResult:
    """Resultado de evaluar una Task contra las políticas activas."""

    decision: PolicyDecision
    reason: str
    policy_id: str
    sandbox_profile: SandboxProfile | None = None


# ─── CapabilityPolicy ────────────────────────────────────────────


class CapabilityPolicyProtocol(Protocol):
    """Protocolo que debe implementar una política de gobernanza."""

    policy_id: str

    def evaluar(
        self,
        task: Any,
        objective: Any,
        context: ExecutionContext,
    ) -> PolicyResult | None: ...


@dataclass(frozen=True)
class CapabilityPolicy:
    """Una política declarativa que permite o deniega capabilities bajo
    condiciones específicas. Las políticas son declarativas y compuestas
    — nunca imperativas."""

    policy_id: str
    capabilities: set[str]
    decision: PolicyDecision
    sandbox_profile: SandboxProfile | None = None
    condition: Callable[[Any, Any, ExecutionContext], bool] | None = None
    reason: str = ""

    def evaluar(
        self,
        task: Any,
        objective: Any,
        context: ExecutionContext,
    ) -> PolicyResult | None:
        """Evalúa la política contra la Task. Retorna ``None`` si la
        política no aplica (capability no cubierta o condición no
        cumplida)."""
        if task.capability_id not in self.capabilities:
            return None
        if self.condition is not None and not self.condition(task, objective, context):
            return None
        return PolicyResult(
            decision=self.decision,
            reason=self.reason or f"policy={self.policy_id}",
            policy_id=self.policy_id,
            sandbox_profile=self.sandbox_profile,
        )


# ─── AuditEntry ──────────────────────────────────────────────────


@dataclass(frozen=True)
class AuditEntry:
    """Entrada inmutable del AuditLog. Cada entrada incluye el hash de la
    anterior, haciendo el log verificable y resistente a manipulación."""

    timestamp: str
    execution_id: str
    event_type: str
    task_id: str | None
    capability_id: str | None
    decision: str
    policy_id: str
    reason: str
    sandbox_profile: str | None
    previous_hash: str
    entry_hash: str = ""
