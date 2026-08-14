"""
eon.planning.replanning
=========================
AutoReplanner: replanificación contextual tras rechazos.

NO es un simple retry. El replanner:
1. Analiza por qué falló la task original
2. Busca skills alternativas en la SkillLibrary
3. Sugiere un plan alternativo modificando la task fallida
4. Aumenta attempt_number y evita repetir el mismo error
"""
from __future__ import annotations

import logging
from typing import Any

from .models import ReplanContext

logger = logging.getLogger("eon.planning.replanning")


class AutoReplanner:
    """Replanificador contextual.

    Uso:
        replanner = AutoReplanner(skill_library=my_skill_lib)
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-3",
            failure_reason="Timeout: tool.python no respondió",
            original_plan_id="plan-1",
            attempt_number=1,
        )
        suggestion = replanner.replan(ctx)
        if suggestion:
            print(suggestion["new_tasks"])
    """

    def __init__(
        self,
        skill_library: Any | None = None,
        failure_patterns: Any | None = None,
        max_attempts: int = 3,
    ) -> None:
        self._skills = skill_library
        self._failures = failure_patterns
        self._max_attempts = max_attempts

    def replan(self, context: ReplanContext) -> dict[str, Any] | None:
        """Genera una sugerencia de replanificación.

        Returns:
            Diccionario con "should_replan", "reason", "new_tasks",
            "modified_tasks", "strategy". None si no se debe replanificar.
        """
        if context.attempt_number >= self._max_attempts:
            return {
                "should_replan": False,
                "reason": f"Máximo de intentos ({self._max_attempts}) alcanzado",
                "new_tasks": [],
                "modified_tasks": [],
                "strategy": "abort",
            }

        # Analizar el tipo de fallo
        failure_type = self._classify_failure(context.failure_reason)
        strategy = self._select_strategy(failure_type, context)

        # Buscar skill alternativa
        skill_suggestion = None
        if self._skills:
            try:
                # Usar la descripción del fallo para buscar skill
                skill = self._skills.match(context.failure_reason)
                if skill:
                    skill_suggestion = skill
            except Exception:
                pass

        # Construir tasks modificadas
        modified_tasks = self._build_modified_tasks(context, strategy, skill_suggestion)

        return {
            "should_replan": True,
            "reason": f"Fallo clasificado como '{failure_type}'. Estrategia: {strategy}",
            "new_tasks": modified_tasks,
            "modified_tasks": modified_tasks,
            "strategy": strategy,
            "skill_suggestion": skill_suggestion.name if skill_suggestion else None,
            "attempt_number": context.attempt_number + 1,
        }

    def _classify_failure(self, reason: str) -> str:
        """Clasifica el tipo de fallo para elegir estrategia."""
        reason_lower = reason.lower()

        if "timeout" in reason_lower or "tiempo" in reason_lower:
            return "timeout"
        if "assert" in reason_lower:
            return "assertion_error"
        if "rate limit" in reason_lower or "429" in reason_lower:
            return "rate_limit"
        if "not found" in reason_lower or "no encontrado" in reason_lower or "404" in reason_lower:
            return "not_found"
        if "permission" in reason_lower or "denied" in reason_lower or "403" in reason_lower:
            return "permission_denied"
        if "syntax" in reason_lower or "parse" in reason_lower:
            return "syntax_error"
        if "connection" in reason_lower or "network" in reason_lower:
            return "network_error"
        return "unknown"

    def _select_strategy(self, failure_type: str, context: ReplanContext) -> str:
        """Selecciona estrategia de replanificación según el tipo de fallo."""
        strategies: dict[str, str] = {
            "timeout": "retry_with_timeout_increase",
            "not_found": "alternative_capability",
            "permission_denied": "skip_or_request_permission",
            "rate_limit": "retry_with_backoff",
            "syntax_error": "fix_and_retry",
            "assertion_error": "alternative_approach",
            "network_error": "retry_with_backoff",
            "unknown": "retry_with_modifications",
        }
        return strategies.get(failure_type, "retry_with_modifications")

    def _build_modified_tasks(
        self,
        context: ReplanContext,
        strategy: str,
        skill_suggestion: Any | None,
    ) -> list[dict[str, Any]]:
        """Construye tasks modificadas según la estrategia."""
        tasks: list[dict[str, Any]] = []

        base_task: dict[str, Any] = {
            "id": f"replan-{context.attempt_number}-{context.failed_task_id}",
            "replaces": context.failed_task_id,
            "strategy": strategy,
        }

        if strategy == "retry_with_timeout_increase":
            base_task["description"] = f"Reintentar {context.failed_task_id} con timeout aumentado"
            base_task["timeout_override"] = 60  # duplicar timeout por defecto
            tasks.append(base_task)

        elif strategy == "alternative_capability":
            base_task["description"] = f"Usar capability alternativa para {context.failed_task_id}"
            base_task["use_alternative"] = True
            if context.available_capabilities:
                base_task["alternative_capabilities"] = context.available_capabilities
            tasks.append(base_task)

        elif strategy == "retry_with_backoff":
            base_task["description"] = f"Reintentar {context.failed_task_id} con backoff"
            base_task["backoff_seconds"] = 5 * (2 ** context.attempt_number)
            tasks.append(base_task)

        elif strategy == "fix_and_retry":
            base_task["description"] = f"Corregir y reintentar {context.failed_task_id}"
            base_task["fix_before_retry"] = True
            tasks.append(base_task)

        elif strategy == "alternative_approach":
            base_task["description"] = f"Enfoque alternativo para {context.failed_task_id}"
            base_task["alternative_approach"] = True
            if skill_suggestion:
                base_task["use_skill"] = skill_suggestion.name
                base_task["plan_data"] = getattr(skill_suggestion, "plan_data", {})
            tasks.append(base_task)

        else:
            base_task["description"] = f"Reintentar {context.failed_task_id} con modificaciones"
            base_task["previous_errors"] = context.previous_errors
            tasks.append(base_task)

        return tasks

    def should_abort(self, context: ReplanContext) -> bool:
        """Determina si se debe abortar la ejecución."""
        return context.attempt_number >= self._max_attempts
