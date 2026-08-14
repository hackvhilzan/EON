"""
eon.guardrails
=================
Tool Guardrails — validación pre/post ejecución de capabilities.

Guardrails son hooks que se ejecutan antes y después de cada llamada
a una capability/tool. Pueden:
- ALLOW: permitir la ejecución
- DENY: bloquear la ejecución
- REQUIRE_APPROVAL: pausar para aprobación humana (HITL)
- WARN: permitir pero registrar una advertencia

Reglas incluidas:
- SecretPatternGuardrail: detecta API keys, tokens, passwords en params
- FilesystemGuardrail: bloquea path traversal y acceso fuera del sandbox
- NetworkGuardrail: bloquea red si el sandbox profile no la permite
- PIIGuardrail: detecta datos personales (email, DNI, tarjeta de crédito)
"""
from __future__ import annotations

from .manager import ToolGuardrailManager
from .models import (
    GuardrailAction,
    GuardrailResult,
    ToolCallContext,
)
from .rules import (
    AllowAllGuardrails,
    FilesystemGuardrail,
    NetworkGuardrail,
    PIIGuardrail,
    SecretPatternGuardrail,
)

__all__ = [
    "GuardrailAction",
    "GuardrailResult",
    "ToolCallContext",
    "ToolGuardrailManager",
    "SecretPatternGuardrail",
    "FilesystemGuardrail",
    "NetworkGuardrail",
    "PIIGuardrail",
    "AllowAllGuardrails",
]
