"""
eon.plugins.base
==================
ToolPlugin — base para plugins de Tools custom.

Un plugin empaqueta una Tool con sus metadatos de gobernanza.
Permite registrar Tools custom sin tocar el código del core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..governance.models import PolicyDecision, SandboxProfile
from ..tools.base_tool import Tool


@dataclass
class ToolPlugin:
    """Empaqueta una Tool con sus metadatos de gobernanza.

    Atributos:
        name: Nombre único del plugin.
        tool: Instancia de Tool registrada.
        capability_id: ID bajo el que se registra en CapabilityMap
            (convención: "tool.<name>").
        default_policy: Política por defecto (ALLOW, DENY, REQUIRES_APPROVAL).
        sandbox_profile: Perfil de sandbox para la Tool.
        version: Versión del plugin.
        description: Descripción humana.
        metadata: Metadatos adicionales.
    """

    name: str
    tool: Tool
    capability_id: str = ""
    default_policy: PolicyDecision = PolicyDecision.DENY
    sandbox_profile: SandboxProfile | None = None
    version: str = "1.0.0"
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id:
            self.capability_id = f"tool.{self.tool.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tool_name": self.tool.name,
            "capability_id": self.capability_id,
            "default_policy": self.default_policy.value,
            "version": self.version,
            "description": self.description,
            "metadata": dict(self.metadata),
        }
