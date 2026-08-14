"""Store del Scheduler (Fase 9). Sin lógica de negocio.

Antes, `InMemorySchedulerStore.crear()` rechazaba con `InvalidPlanError` una
Task que ya perteneciera a otro Plan -- una decisión de dominio (la
invariante "una Task pertenece a un único Plan") viviendo dentro de lo que
este mismo docstring afirmaba que era persistencia pura. Corregido: el Store
solo expone `plan_de_task_o_none()`, una consulta de lectura sin efectos;
decidir si eso constituye una violación y lanzar la excepción es
responsabilidad de `SchedulerManager.crear()`, el único punto de escritura
del dominio (mismo patrón que `ObjectiveStore`/`PlanStore`: el Store persiste
lo que el Manager ya decidió que es legal, nunca decide por su cuenta)."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from .exceptions import SchedulerAlreadyExistsError, SchedulerNotFoundError, TaskNotFoundError
from .models import SchedulerRun


class SchedulerStore(ABC):
    """Persistencia pura de `SchedulerRun`. No valida reglas de dominio."""

    @abstractmethod
    def crear(self, run: SchedulerRun) -> SchedulerRun: ...

    @abstractmethod
    def obtener(self, plan_id: str) -> SchedulerRun: ...

    @abstractmethod
    def guardar(self, run: SchedulerRun) -> SchedulerRun: ...

    @abstractmethod
    def listar(self) -> list[SchedulerRun]: ...

    @abstractmethod
    def plan_de_task(self, task_id: str) -> str: ...

    @abstractmethod
    def plan_de_task_o_none(self, task_id: str) -> str | None:
        """Como `plan_de_task`, pero sin lanzar: devuelve `None` si el
        `task_id` no está indexado todavía. Consulta pura, sin decisión de
        dominio -- quien llama decide qué significa el resultado."""
        ...


class InMemorySchedulerStore(SchedulerStore):
    """Implementación en memoria, con índice auxiliar task_id -> plan_id.

    Una Task pertenece a un único Plan (invariante de Fase 9), por lo que
    el índice se construye una sola vez, en `crear()`, y nunca se
    reescribe para un `task_id` ya indexado.
    """

    def __init__(self) -> None:
        self._runs: dict[str, SchedulerRun] = {}
        self._indice_tasks: dict[str, str] = {}
        self._lock = threading.Lock()

    def crear(self, run: SchedulerRun) -> SchedulerRun:
        """Identidad de `plan_id` (clave primaria): duplicado -> error. Esto
        no es una regla de dominio de Fase 9, es la misma garantía de
        integridad que cualquier Store de este proyecto ofrece sobre su
        propia clave (ver `InMemoryObjectiveStore.create()`). La invariante
        real de dominio ("una Task pertenece a un único Plan") ya no se
        decide aquí -- ver `plan_de_task_o_none()` y `SchedulerManager.crear()`."""
        with self._lock:
            if run.plan_id in self._runs:
                raise SchedulerAlreadyExistsError(run.plan_id)
            self._runs[run.plan_id] = run
            for task_id in run.tasks:
                self._indice_tasks[task_id] = run.plan_id
            return run

    def obtener(self, plan_id: str) -> SchedulerRun:
        with self._lock:
            run = self._runs.get(plan_id)
            if run is None:
                raise SchedulerNotFoundError(plan_id)
            return run

    def guardar(self, run: SchedulerRun) -> SchedulerRun:
        with self._lock:
            if run.plan_id not in self._runs:
                raise SchedulerNotFoundError(run.plan_id)
            self._runs[run.plan_id] = run
            return run

    def listar(self) -> list[SchedulerRun]:
        with self._lock:
            return list(self._runs.values())

    def plan_de_task(self, task_id: str) -> str:
        with self._lock:
            plan_id = self._indice_tasks.get(task_id)
            if plan_id is None:
                raise TaskNotFoundError(task_id)
            return plan_id

    def plan_de_task_o_none(self, task_id: str) -> str | None:
        with self._lock:
            return self._indice_tasks.get(task_id)
