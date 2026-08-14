"""
Máquina de estados explícita del Scheduler (Fase 9).

Dos máquinas independientes:

1. `SchedulerState` — ciclo de vida de la ejecución de un Plan.
2. `TaskExecutionState` — ciclo de vida de cada Task dentro de ese Plan.

Toda transición no listada aquí es ilegal y lanza
`IllegalSchedulerTransitionError`. No hay excepciones ni casos especiales
fuera de estas tablas.
"""

from __future__ import annotations

from .events import (
    SCHEDULER_CANCELADO,
    SCHEDULER_INICIADO,
    SCHEDULER_PAUSADO,
    SCHEDULER_REANUDADO,
    TASK_BLOCKED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_READY,
    TASK_RUNNING,
)
from .exceptions import IllegalSchedulerTransitionError
from .models import SchedulerState, TaskExecutionState

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

# Nota de diseño: RUNNING es alcanzable desde dos operaciones distintas
# (`iniciar` desde IDLE, `reanudar` desde PAUSED) que deben emitir eventos
# distintos. Una tabla indexada solo por (origen, destino) no puede
# distinguir esos dos casos (ambos son "-> RUNNING"), así que la tabla se
# indexa por (operación, origen) -> (destino, evento). Cada método público
# de `SchedulerManager` (`iniciar`, `pausar`, `reanudar`, `cancelar`) pasa
# su propio nombre de operación.

_OPERACIONES_SCHEDULER: dict[tuple[str, SchedulerState], tuple[SchedulerState, str]] = {
    ("iniciar", SchedulerState.IDLE): (SchedulerState.RUNNING, SCHEDULER_INICIADO),
    ("pausar", SchedulerState.RUNNING): (SchedulerState.PAUSED, SCHEDULER_PAUSADO),
    ("reanudar", SchedulerState.PAUSED): (SchedulerState.RUNNING, SCHEDULER_REANUDADO),
    ("cancelar", SchedulerState.IDLE): (SchedulerState.STOPPED, SCHEDULER_CANCELADO),
    ("cancelar", SchedulerState.RUNNING): (SchedulerState.STOPPED, SCHEDULER_CANCELADO),
    ("cancelar", SchedulerState.PAUSED): (SchedulerState.STOPPED, SCHEDULER_CANCELADO),
}


def validar_operacion_scheduler(operacion: str, origen: SchedulerState) -> tuple[SchedulerState, str]:
    """Valida `operacion` (`iniciar`/`pausar`/`reanudar`/`cancelar`) desde el
    estado `origen` y devuelve `(destino, evento)`. Lanza
    `IllegalSchedulerTransitionError` si la operación no es válida desde
    ese estado."""
    resultado = _OPERACIONES_SCHEDULER.get((operacion, origen))
    if resultado is None:
        raise IllegalSchedulerTransitionError(origen.value, operacion=operacion, contexto="scheduler")
    return resultado


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

_TRANSICIONES_TASK: dict[tuple[TaskExecutionState, TaskExecutionState], str] = {
    (TaskExecutionState.PENDING, TaskExecutionState.READY): TASK_READY,
    (TaskExecutionState.READY, TaskExecutionState.RUNNING): TASK_RUNNING,
    (TaskExecutionState.RUNNING, TaskExecutionState.COMPLETED): TASK_COMPLETED,
    (TaskExecutionState.RUNNING, TaskExecutionState.FAILED): TASK_FAILED,
    (TaskExecutionState.PENDING, TaskExecutionState.BLOCKED): TASK_BLOCKED,
    # SCHEDULER.md v1.0 ("cancelar()"): al cancelar, TODA Task que no esté ya
    # en COMPLETED/FAILED/CANCELLED pasa a CANCELLED -- incluida RUNNING, para
    # que ninguna Task quede huérfana en un estado no terminal después de que
    # el Scheduler entra en STOPPED (marcar_running/completed/failed exigen
    # scheduler RUNNING, así que tras STOPPED no hay otra forma de resolverla).
    # Ninguna emite evento propio en el EventBus (ver nota más abajo).
    (TaskExecutionState.PENDING, TaskExecutionState.CANCELLED): None,
    (TaskExecutionState.READY, TaskExecutionState.CANCELLED): None,
    (TaskExecutionState.BLOCKED, TaskExecutionState.CANCELLED): None,
    (TaskExecutionState.RUNNING, TaskExecutionState.CANCELLED): None,
}


def validar_transicion_task(origen: TaskExecutionState, destino: TaskExecutionState) -> str | None:
    """Valida una transición de Task y devuelve el evento a publicar.

    Las transiciones hacia CANCELLED no publican un evento propio: son
    parte de `cancelar(plan_id)`, que publica un único `scheduler_cancelado`
    con el detalle de las Tasks canceladas (ver `scheduler.py`), ya que
    `task_cancelled` no forma parte del catálogo de eventos de Fase 9.
    """
    clave = (origen, destino)
    if clave not in _TRANSICIONES_TASK:
        raise IllegalSchedulerTransitionError(origen.value, destino.value, contexto="task")
    return _TRANSICIONES_TASK[clave]
