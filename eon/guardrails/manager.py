"""
eon.guardrails.manager
========================
ToolGuardrailManager — orquesta múltiples guardrails en cadena.

Ejecuta todos los guardrails registrados en orden. La acción más
restrictiva gana: DENY > REQUIRE_APPROVAL > WARN > ALLOW.

Integración con AuditLog: cada decisión se registra para auditoría.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import GuardrailAction, GuardrailResult, ToolCallContext
from .rules import ToolGuardrail

logger = logging.getLogger("eon.guardrails")

# Prioridad de acciones: más restrictiva gana
_ACTION_PRIORITY = {
    GuardrailAction.DENY: 4,
    GuardrailAction.REQUIRE_APPROVAL: 3,
    GuardrailAction.WARN: 2,
    GuardrailAction.ALLOW: 1,
}


class ToolGuardrailManager:
    """Gestiona una cadena de guardrails y evalúa llamadas a tools.

    Uso:
        manager = ToolGuardrailManager()
        manager.add(SecretPatternGuardrail())
        manager.add(FilesystemGuardrail(allowed_root="/sandbox"))

        result = manager.pre_execute(ctx)
        if result.is_denied:
            # Bloquear ejecución
    """

    def __init__(self, audit_log: Any | None = None) -> None:
        self._guardrails: list[ToolGuardrail] = []
        self._audit_log = audit_log

    def add(self, guardrail: ToolGuardrail) -> ToolGuardrailManager:
        """Añade un guardrail a la cadena."""
        self._guardrails.append(guardrail)
        return self

    def pre_execute(self, ctx: ToolCallContext) -> GuardrailResult:
        """Evalúa todos los guardrails pre-ejecución.

        La acción más restrictiva gana. Si algún guardrail hace DENY,
        se devuelve inmediatamente.
        """
        final_result = GuardrailResult(
            action=GuardrailAction.ALLOW,
            guardrail_name="manager",
        )
        redacted_params = ctx.params

        for guardrail in self._guardrails:
            try:
                # Usar params redacted del guardrail anterior si los modificó
                ctx.params = redacted_params
                result = guardrail.pre_execute(ctx)
                if result.redacted_params:
                    redacted_params = result.redacted_params

                self._audit(ctx, "pre", result)

                if result.is_denied:
                    return result

                if _ACTION_PRIORITY[result.action] > _ACTION_PRIORITY[final_result.action]:
                    final_result = result
            except Exception:
                logger.exception("Error en guardrail %s.pre_execute", guardrail.name)

        # Aplicar redacción final
        if redacted_params != ctx.params:
            final_result.redacted_params = redacted_params

        return final_result

    def post_execute(self, ctx: ToolCallContext, result: Any) -> GuardrailResult:
        """Evalúa todos los guardrails post-ejecución."""
        final_result = GuardrailResult(
            action=GuardrailAction.ALLOW,
            guardrail_name="manager",
        )

        for guardrail in self._guardrails:
            try:
                gr_result = guardrail.post_execute(ctx, result)
                self._audit(ctx, "post", gr_result)

                if gr_result.is_denied:
                    return gr_result

                if _ACTION_PRIORITY[gr_result.action] > _ACTION_PRIORITY[final_result.action]:
                    final_result = gr_result
            except Exception:
                logger.exception("Error en guardrail %s.post_execute", guardrail.name)

        return final_result

    def _audit(
        self,
        ctx: ToolCallContext,
        phase: str,
        result: GuardrailResult,
    ) -> None:
        """Registra la decisión del guardrail en el audit log."""
        if self._audit_log is None:
            return
        try:
            self._audit_log.record(
                event=f"guardrail.{phase}",
                action=result.action.value,
                guardrail=result.guardrail_name,
                reason=result.reason,
                capability_id=ctx.capability_id,
                task_id=ctx.task_id,
                execution_id=ctx.execution_id,
            )
        except Exception:
            logger.debug("Audit log no disponible o falló", exc_info=True)

    @property
    def guardrail_count(self) -> int:
        return len(self._guardrails)
