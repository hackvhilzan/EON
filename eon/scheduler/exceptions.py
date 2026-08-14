"""Excepciones del dominio Scheduler (Fase 9)."""

from __future__ import annotations


class SchedulerError(Exception):
    """Base de todas las excepciones del Scheduler."""


class SchedulerNotFoundError(SchedulerError):
    """No existe un Scheduler (ejecución) para el `plan_id` indicado."""

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        super().__init__(f"No existe un scheduler para plan_id={plan_id!r}")


class SchedulerAlreadyExistsError(SchedulerError):
    """Ya existe un Scheduler creado para ese `plan_id`."""

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        super().__init__(f"Ya existe un scheduler para plan_id={plan_id!r}")


class TaskNotFoundError(SchedulerError):
    """No existe una Task con ese `task_id` en ningún Plan conocido."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"No existe la task task_id={task_id!r}")


class InvalidPlanError(SchedulerError):
    """El Plan-like recibido en `crear()` no es válido para el Scheduler.

    Cubre: Plan sin tasks, tasks duplicadas, o dependencias que apuntan a
    una Task inexistente dentro del propio Plan.
    """


class DependencyCycleError(SchedulerError):
    """El grafo de dependencias de las Tasks de un Plan contiene un ciclo."""

    def __init__(self, ciclo: tuple[str, ...]) -> None:
        self.ciclo = ciclo
        super().__init__(f"Ciclo de dependencias detectado: {' -> '.join(ciclo)}")


class IllegalSchedulerTransitionError(SchedulerError):
    """Transición ilegal de estado (del Scheduler o de una Task), u operación
    no aplicable desde el estado actual del Scheduler.

    Dos usos reales en el dominio, que antes compartían el mismo parámetro
    `destino` para cosas distintas (C2, corregido): cuando se conoce el
    estado de destino que se intentó alcanzar (transición de Task, o de
    Scheduler hacia un estado concreto como RUNNING), se pasa `destino`.
    Cuando lo que falla es una *operación* (`iniciar`/`pausar`/`reanudar`/
    `cancelar`) que no está definida desde el estado actual -- y por tanto
    no hay un único "destino que se intentó" que mostrar -- se pasa
    `operacion` en su lugar. Antes ambos casos reutilizaban `destino`,
    produciendo mensajes como "'idle' -> 'pausar'": una supuesta transición
    de estado hacia lo que en realidad es un verbo, no un estado.
    """

    def __init__(
        self,
        origen: str,
        destino: str | None = None,
        *,
        operacion: str | None = None,
        contexto: str = "scheduler",
    ) -> None:
        if (destino is None) == (operacion is None):
            raise ValueError("Debe indicarse exactamente uno de: destino, operacion.")
        self.origen = origen
        self.destino = destino
        self.operacion = operacion
        self.contexto = contexto
        if operacion is not None:
            mensaje = f"Operación no válida en {contexto}: {operacion!r} no es aplicable desde el estado {origen!r}"
        else:
            mensaje = f"Transición ilegal en {contexto}: {origen!r} -> {destino!r}"
        super().__init__(mensaje)
