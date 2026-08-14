"""
Resolvedor de dependencias del Scheduler (Fase 9).

Puro: no muta nada, no conoce el EventBus, no conoce el Store. Opera
siempre sobre un `dict[str, TaskExecutionRecord]` (el `.tasks` de un
`SchedulerRun`) y su `orden` de inserción, que es la fuente de verdad para
que cualquier resultado (colas, listas de bloqueadas, etc.) sea
determinista.

Reutiliza el mismo enfoque que `TaskGraph` del Planner (validación de
existencia de dependencias + detección de ciclos por DFS con pila de
recursión), adaptado a los tipos propios del Scheduler para no importar
`eon.planner` (ver nota de diseño en `models.py`).
"""

from __future__ import annotations

from .exceptions import DependencyCycleError, InvalidPlanError
from .models import TaskExecutionRecord, TaskExecutionState

Tasks = dict[str, TaskExecutionRecord]


def validar_grafo(tasks: Tasks, orden: tuple[str, ...]) -> None:
    """Valida que el grafo de dependencias sea consistente y acíclico.

    Lanza `InvalidPlanError` si una Task no existe o si hay ids duplicados
    (ausentes de `tasks` porque ya se habrían colapsado en el dict), y
    `DependencyCycleError` si existe un ciclo.
    """
    if not tasks:
        raise InvalidPlanError("El Plan no contiene ninguna Task")

    for task_id, record in tasks.items():
        for dep in record.depende_de:
            if dep not in tasks:
                raise InvalidPlanError(f"La Task {task_id!r} depende de {dep!r}, que no existe en el Plan")
            if dep == task_id:
                raise InvalidPlanError(f"La Task {task_id!r} depende de sí misma")

    _detectar_ciclo(tasks, orden)


def _detectar_ciclo(tasks: Tasks, orden: tuple[str, ...]) -> None:
    EN_PROGRESO, VISITADO = 1, 2
    estado_visita: dict[str, int] = {}
    pila: list[str] = []

    def visitar(task_id: str) -> None:
        estado_visita[task_id] = EN_PROGRESO
        pila.append(task_id)
        for dep in tasks[task_id].depende_de:
            marca = estado_visita.get(dep)
            if marca == EN_PROGRESO:
                inicio = pila.index(dep)
                raise DependencyCycleError(tuple(pila[inicio:] + [dep]))
            if marca != VISITADO:
                visitar(dep)
        pila.pop()
        estado_visita[task_id] = VISITADO

    for task_id in orden:
        if estado_visita.get(task_id) != VISITADO:
            visitar(task_id)


def dependencias_satisfechas(task_id: str, tasks: Tasks) -> bool:
    """True si todas las dependencias de `task_id` están COMPLETED."""
    record = tasks[task_id]
    return all(tasks[dep].estado is TaskExecutionState.COMPLETED for dep in record.depende_de)


def tasks_ready(tasks: Tasks, orden: tuple[str, ...]) -> list[str]:
    """Ids en PENDING cuyas dependencias ya están todas COMPLETED.

    Orden determinista: el de inserción original del Plan.
    """
    return [
        task_id
        for task_id in orden
        if tasks[task_id].estado is TaskExecutionState.PENDING and dependencias_satisfechas(task_id, tasks)
    ]


def descendientes(tasks: Tasks, task_id: str, orden: tuple[str, ...]) -> list[str]:
    """Ids de todas las Tasks que dependen, directa o transitivamente, de
    `task_id`. Orden determinista (el de inserción del Plan).
    """
    directos: dict[str, list[str]] = {tid: [] for tid in tasks}
    for tid, record in tasks.items():
        for dep in record.depende_de:
            directos[dep].append(tid)

    vistos: set[str] = set()
    pendientes = list(directos[task_id])
    while pendientes:
        actual = pendientes.pop()
        if actual in vistos:
            continue
        vistos.add(actual)
        pendientes.extend(directos[actual])

    return [tid for tid in orden if tid in vistos]


def tasks_bloqueadas_por(tasks: Tasks, task_id_fallida: str, orden: tuple[str, ...]) -> list[str]:
    """Ids de las Tasks que deben pasar a BLOCKED porque `task_id_fallida`
    fracasó. Solo se bloquean descendientes que siguen en PENDING: una
    Task ya RUNNING, COMPLETED o en cualquier otro estado terminal no se
    reconsidera (invariante: no hay transiciones silenciosas ni vueltas
    atrás desde estados terminales).
    """
    return [
        tid for tid in descendientes(tasks, task_id_fallida, orden) if tasks[tid].estado is TaskExecutionState.PENDING
    ]
