"""Catálogo de eventos publicados por el Scheduler (Fase 9).

El Scheduler nunca produce transiciones silenciosas: toda transición de
estado (del Scheduler o de una Task dentro de él) publica exactamente uno
de estos eventos en el EventBus.
"""

from __future__ import annotations

SCHEDULER_CREADO = "scheduler_creado"
SCHEDULER_INICIADO = "scheduler_iniciado"
SCHEDULER_PAUSADO = "scheduler_pausado"
SCHEDULER_REANUDADO = "scheduler_reanudado"
SCHEDULER_CANCELADO = "scheduler_cancelado"

TASK_READY = "task_ready"
TASK_RUNNING = "task_running"
TASK_COMPLETED = "task_completed"
TASK_FAILED = "task_failed"
TASK_BLOCKED = "task_blocked"

TODOS = frozenset(
    {
        SCHEDULER_CREADO,
        SCHEDULER_INICIADO,
        SCHEDULER_PAUSADO,
        SCHEDULER_REANUDADO,
        SCHEDULER_CANCELADO,
        TASK_READY,
        TASK_RUNNING,
        TASK_COMPLETED,
        TASK_FAILED,
        TASK_BLOCKED,
    }
)
