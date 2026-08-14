"""
eon.workers.executor
=======================
TaskExecutor — ejecuta una Task invocando la capability inyectada.

El `EjecutorDeCapability` (`Callable[[str, dict], bool]`) es el contrato
congelado v1.0 de WORKERS.md: recibe `(capability_id, parametros)` y
devuelve `True`/`False`. Nunca lanza excepciones hacia el llamador —
todo fallo se traduce a `False`, y `TaskExecutor` emite `TASK_COMPLETADA`
o `TASK_FALLIDA` según el resultado.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import events

logger = logging.getLogger("eon.workers.executor")

EjecutorDeCapability = Callable[[str, dict], bool]


class TaskExecutor:
    """Ejecuta una Task invocando la capability inyectada.

    No decide qué Worker ejecuta la Task — eso lo hace `Dispatcher`.
    Solo invoca `ejecutar(capability_id, parametros)` y reporta el
    resultado vía EventBus.

    Fase 2.5: si se inyecta un `guardrails_manager`, se ejecutan
    los guardrails pre/post. Si no se inyecta, comportamiento
    original (sin guardrails) — los 504 tests existentes no se ven
    afectados.
    """

    def __init__(
        self,
        event_bus: Any,
        ejecutar: EjecutorDeCapability | None = None,
        guardrails_manager: Any | None = None,
    ) -> None:
        self._events = event_bus
        self._ejecutar = ejecutar or (lambda *_: True)
        self._guardrails = guardrails_manager

    def ejecutar(self, worker_id: str, task: Any) -> bool:
        """Ejecuta `task` con el Worker `worker_id`.

        Emite `TASK_COMPLETADA` si la capability devuelve `True`,
        `TASK_FALLIDA` en caso contrario o excepción.
        """
        capability_id = task.capability_id
        parametros = dict(task.parametros) if hasattr(task, "parametros") else {}
        # Propagar el task_id para que los executors personalizados puedan
        # comportarse de forma distinta por task (ej. fallo controlado, retry).
        parametros.setdefault("task_id", task.id)

        # Fase 2.5: Guardrails pre-ejecución
        if self._guardrails is not None:
            from ..guardrails.models import ToolCallContext

            ctx = ToolCallContext(
                capability_id=capability_id,
                params=parametros,
                task_id=task.id,
                worker_id=worker_id,
            )
            pre_result = self._guardrails.pre_execute(ctx)
            if pre_result.redacted_params:
                parametros = pre_result.redacted_params
            if pre_result.is_denied:
                logger.warning(
                    "Guardrail DENY en capability '%s': %s",
                    capability_id,
                    pre_result.reason,
                )
                self._events.emit(
                    events.TASK_FALLIDA,
                    worker_id=worker_id,
                    task_id=task.id,
                )
                return False

        try:
            ok = self._ejecutar(capability_id, parametros)
        except Exception as exc:
            logger.exception("Error ejecutando capability '%s': %s", capability_id, exc)
            self._events.emit(events.TASK_FALLIDA, worker_id=worker_id, task_id=task.id)
            return False

        # Fase 2.5: Guardrails post-ejecución
        if self._guardrails is not None and ok:
            post_result = self._guardrails.post_execute(ctx, ok)
            if post_result.is_denied:
                logger.warning(
                    "Guardrail post-DENY en capability '%s': %s",
                    capability_id,
                    post_result.reason,
                )
                self._events.emit(
                    events.TASK_FALLIDA,
                    worker_id=worker_id,
                    task_id=task.id,
                )
                return False

        if ok:
            self._events.emit(events.TASK_COMPLETADA, worker_id=worker_id, task_id=task.id)
            return True
        self._events.emit(events.TASK_FALLIDA, worker_id=worker_id, task_id=task.id)
        return False
