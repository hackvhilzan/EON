"""
Reglas de guardrail incluidas en EON.

Cada regla implementa la interfaz ToolGuardrail con pre_execute y post_execute.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import GuardrailAction, GuardrailResult, ToolCallContext


class ToolGuardrail:
    """Interfaz base para todos los guardrails."""

    name: str = "base"

    def pre_execute(self, ctx: ToolCallContext) -> GuardrailResult:
        return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)

    def post_execute(self, ctx: ToolCallContext, result: Any) -> GuardrailResult:
        return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)


class AllowAllGuardrails(ToolGuardrail):
    """Guardrail que permite todo. Usado por defecto para no romper tests."""

    name = "allow-all"


# ─── Secret Pattern Guardrail ──────────────────────────────


class SecretPatternGuardrail(ToolGuardrail):
    """Detecta secrets en los parámetros de entrada.

    Patrones detectados:
    - API keys (sk-, AKIA, ghp_, etc.)
    - Tokens de acceso (Bearer, jwt)
    - Private keys (-----BEGIN)
    - Passwords en params (password, passwd, pwd, secret)
    """

    name = "secret-detection"

    SECRET_PATTERNS = [
        (r"sk-[a-zA-Z0-9]{20,}", "OpenAI API key"),
        (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
        (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
        (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
        (r"-----BEGIN [A-Z ]+PRIVATE KEY-----", "Private key"),
        (r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*", "Bearer token"),
        (r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", "JWT token"),
        (r"xox[baprs]-[a-zA-Z0-9-]+", "Slack token"),
    ]

    SECRET_KEY_NAMES = {"password", "passwd", "pwd", "secret", "api_key", "apikey", "token", "private_key"}

    def pre_execute(self, ctx: ToolCallContext) -> GuardrailResult:
        found = self._scan_secrets(ctx.params)
        if found:
            return GuardrailResult(
                action=GuardrailAction.DENY,
                reason=f"Secret detectado en parámetros: {found}",
                guardrail_name=self.name,
                redacted_params=self._redact(ctx.params),
                metadata={"secrets_found": found},
            )
        return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)

    def post_execute(self, ctx: ToolCallContext, result: Any) -> GuardrailResult:
        if isinstance(result, dict):
            found = self._scan_secrets(result)
            if found:
                return GuardrailResult(
                    action=GuardrailAction.WARN,
                    reason=f"Posible secret en output: {found}",
                    guardrail_name=self.name,
                    metadata={"secrets_in_output": found},
                )
        return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)

    def _scan_secrets(self, data: Any) -> list[str]:
        found: list[str] = []
        text = str(data)
        for pattern, label in self.SECRET_PATTERNS:
            if re.search(pattern, text):
                found.append(label)
        # Check secret key names
        if isinstance(data, dict):
            for key in data:
                if key.lower() in self.SECRET_KEY_NAMES:
                    val = str(data[key])
                    if val and len(val) > 3:
                        found.append(f"secret_key:{key}")
        return found

    def _redact(self, params: dict) -> dict:
        redacted = {}
        for k, v in params.items():
            if k.lower() in self.SECRET_KEY_NAMES:
                redacted[k] = "***REDACTED***"
            elif isinstance(v, str):
                text = v
                for pattern, _ in self.SECRET_PATTERNS:
                    text = re.sub(pattern, "***REDACTED***", text)
                redacted[k] = text
            else:
                redacted[k] = v
        return redacted


# ─── Filesystem Guardrail ──────────────────────────────────


class FilesystemGuardrail(ToolGuardrail):
    """Bloquea acceso al filesystem fuera del sandbox.

    Valida que los paths en los parámetros estén dentro del
    directorio permitido por el sandbox profile.
    """

    name = "filesystem-protection"

    def __init__(self, allowed_root: str | Path | None = None) -> None:
        self._allowed_root = Path(allowed_root).resolve() if allowed_root else None

    def pre_execute(self, ctx: ToolCallContext) -> GuardrailResult:
        if self._allowed_root is None:
            return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)

        # Buscar paths en los parámetros
        for _key, value in ctx.params.items():
            if not isinstance(value, str):
                continue
            if not any(c in value for c in ("/", "\\", "..")):
                continue
            # Intentar resolver como path
            try:
                resolved = Path(value).resolve()
                if not resolved.is_relative_to(self._allowed_root):
                    return GuardrailResult(
                        action=GuardrailAction.DENY,
                        reason=f"Path fuera del sandbox: {value} -> {resolved}",
                        guardrail_name=self.name,
                        metadata={"blocked_path": str(resolved)},
                    )
            except (ValueError, OSError):
                continue

        return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)


# ─── Network Guardrail ────────────────────────────────────


class NetworkGuardrail(ToolGuardrail):
    """Bloquea capabilities que necesitan red si el sandbox no la permite.

    Si el sandbox profile tiene network_allowed=False y la capability
    declara que necesita red, se bloquea.
    """

    name = "network-protection"

    NETWORK_CAPABILITIES = {
        "tool.http_get",
        "tool.http_post",
        "tool.web_fetch",
        "tool.api_call",
        "tool.email_send",
    }

    def pre_execute(self, ctx: ToolCallContext) -> GuardrailResult:
        profile = ctx.sandbox_profile
        if profile is None:
            return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)

        network_allowed = getattr(profile, "network_allowed", True)
        if network_allowed:
            return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)

        if ctx.capability_id in self.NETWORK_CAPABILITIES:
            return GuardrailResult(
                action=GuardrailAction.DENY,
                reason=f"Capability '{ctx.capability_id}' requiere red pero el sandbox la bloquea",
                guardrail_name=self.name,
                metadata={"capability": ctx.capability_id},
            )

        return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)


# ─── PII Guardrail ────────────────────────────────────────


class PIIGuardrail(ToolGuardrail):
    """Detecta datos personales sensibles en los parámetros.

    Detecta:
    - Emails
    - DNIs (españoles)
    - Números de tarjeta de crédito
    - Teléfonos
    """

    name = "pii-detection"

    PII_PATTERNS = [
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "email"),
        (r"\b\d{8}[A-Za-z]\b", "DNI"),
        (r"\b(?:\d[ -]*?){13,16}\b", "credit_card"),
        (r"\b(?:\+34|0034)?[6-9]\d{8}\b", "phone_es"),
    ]

    def __init__(self, block_pii: bool = False) -> None:
        self._block = block_pii

    def pre_execute(self, ctx: ToolCallContext) -> GuardrailResult:
        found = self._scan_pii(ctx.params)
        if found:
            action = GuardrailAction.DENY if self._block else GuardrailAction.WARN
            return GuardrailResult(
                action=action,
                reason=f"PII detectado en parámetros: {found}",
                guardrail_name=self.name,
                metadata={"pii_found": found},
            )
        return GuardrailResult(action=GuardrailAction.ALLOW, guardrail_name=self.name)

    def _scan_pii(self, data: Any) -> list[str]:
        found: list[str] = []
        text = str(data)
        for pattern, label in self.PII_PATTERNS:
            if re.search(pattern, text):
                found.append(label)
        return found
