"""Modelos del sistema de guardrails."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GuardrailAction(str, Enum):
    """Acción que toma un guardrail tras evaluar una llamada."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    WARN = "warn"


@dataclass
class GuardrailResult:
    """Resultado de evaluar un guardrail."""

    action: GuardrailAction
    reason: str = ""
    guardrail_name: str = ""
    redacted_params: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        return self.action in (GuardrailAction.ALLOW, GuardrailAction.WARN)

    @property
    def is_denied(self) -> bool:
        return self.action == GuardrailAction.DENY

    @property
    def needs_approval(self) -> bool:
        return self.action == GuardrailAction.REQUIRE_APPROVAL


@dataclass
class ToolCallContext:
    """Contexto de una llamada a una capability/tool."""

    capability_id: str
    params: dict[str, Any]
    execution_id: str | None = None
    task_id: str | None = None
    worker_id: str | None = None
    sandbox_profile: Any | None = None  # SandboxProfile
