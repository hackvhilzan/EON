"""
eon.scheduler.scheduler
========================
Toda la lógica de dominio del Scheduler. Único punto de escritura:
`SchedulerManager` (FASE9 §Responsabilidades).

El Scheduler decide QUÉ Task puede ejecutarse en cada momento a partir del
grafo de dependencias de un Plan. No ejecuta Tasks, no conoce Tools, LLM
ni infraestructura, no verifica resultados, no modifica Objetivos ni
Planes (FASE9, contexto).

Decisiones de diseño no explícitas en el contrato, documentadas aquí para
que Fase 10 (Workers) no tenga que adivinarlas:

1. `crear(plan)` acepta cualquier objeto Plan-like (duck typing: `.id` y
   `.tasks`, cada Task-like con `.id` y `.depende_de`). El Scheduler
   construye sus propios `TaskExecutionRecord` inmediatamente y nunca
   vuelve a tocar el objeto Plan original -- no importa `eon.planner`.

2. La cola READY se recalcula de forma continua, no solo tras
   `iniciar()`: en `crear()` ya se marca READY toda Task sin
   dependencias, y tras cada `marcar_completed()` se recalculan las
   Tasks PENDING que hayan quedado con todas sus dependencias
   COMPLETED. `iniciar()`/`pausar()`/`reanudar()` solo controlan si el
   Scheduler admite `marcar_running()` (no controlan el cálculo de
   READY, que es una propiedad estructural del grafo).

3. `marcar_running`, `marcar_completed` y `marcar_failed` exigen que el
   Scheduler esté en RUNNING: no tiene sentido reportar progreso de
   ejecución mientras el propio Scheduler está en pausa o detenido.

4. `cancelar(plan_id)` detiene el Scheduler y cancela en cascada TODA Task
   que no esté ya en COMPLETED, FAILED o CANCELLED -- incluida RUNNING
   (SCHEDULER.md v1.0, corrección de contrato: dejar una Task RUNNING sin
   tocar la deja huérfana, porque tras STOPPED ninguna operación
   `marcar_*` la puede resolver ya). No existe evento `task_cancelled` en
   el catálogo de Fase 9, así que la cascada se reporta como parte del
   único evento `scheduler_cancelado` (campo `extra.tasks_cancelados`),
   no como N eventos por Task -- decisión consistente con "todo cambio
   produce exactamente un evento" al nivel de la operación `cancelar`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from . import dependency_resolver as resolver
from . import events, validators
from .exceptions import IllegalSchedulerTransitionError, InvalidPlanError, TaskNotFoundError
from .models import SchedulerRun, SchedulerSnapshot, SchedulerState, TaskExecutionRecord, TaskExecutionState
from .scheduler_store import SchedulerStore


class _TaskLike(Protocol):
    id: str
    depende_de: Iterable[str]


class _PlanLike(Protocol):
    id: str
    tasks: Iterable[_TaskLike]


class SchedulerManager:
    def __init__(self, store: SchedulerStore, event_bus: Any):
        self._store = store
        self._events = event_bus

    # ---- utilidades internas ----

    def _require(self, plan_id: str) -> SchedulerRun:
        return self._store.obtener(plan_id)

    def _require_task(self, task_id: str) -> tuple[SchedulerRun, TaskExecutionRecord]:
        plan_id = self._store.plan_de_task(task_id)
        run = self._store.obtener(plan_id)
        record = run.tasks.get(task_id)
        if record is None:  # pragma: no cover - invariante de índice
            raise TaskNotFoundError(task_id)
        return run, record

    def _transicion_scheduler(
        self, run: SchedulerRun, operacion: str, motivo: str | None = None, **extra: Any
    ) -> SchedulerRun:
        origen = run.estado
        destino, evento = validators.validar_operacion_scheduler(operacion, origen)
        run.estado = destino
        run.registrar(evento, de=origen.value, a=destino.value, motivo=motivo, **extra)
        self._store.guardar(run)
        self._events.emit(evento, plan_id=run.plan_id, de=origen.value, a=destino.value, motivo=motivo, **extra)
        return run

    def _transicion_task(
        self,
        run: SchedulerRun,
        record: TaskExecutionRecord,
        destino: TaskExecutionState,
        **extra: Any,
    ) -> str | None:
        origen = record.estado
        evento = validators.validar_transicion_task(origen, destino)
        record.estado = destino
        run.registrar(
            evento or "task_cancelled",
            de=origen.value,
            a=destino.value,
            task_id=record.task_id,
            **extra,
        )
        if evento is not None:
            self._events.emit(
                evento, plan_id=run.plan_id, task_id=record.task_id, de=origen.value, a=destino.value, **extra
            )
        return evento

    def _actualizar_ready(self, run: SchedulerRun) -> None:
        """Marca READY toda Task en PENDING cuyas dependencias ya están
        COMPLETED. Misma llamada tanto justo tras `crear()` (donde marca las
        que nacen sin dependencias) como tras cada `marcar_completed()`
        (donde recalcula lo que quedó desbloqueado) -- es la misma operación
        estructural sobre el grafo en ambos casos (antes duplicada en dos
        métodos idénticos, `_marcar_ready_iniciales`/`_recalcular_ready`)."""
        for task_id in resolver.tasks_ready(run.tasks, run.orden):
            self._transicion_task(run, run.tasks[task_id], TaskExecutionState.READY)

    # ---- API pública (FASE9 §Responsabilidades) ----

    def crear(self, plan: _PlanLike) -> SchedulerRun:
        orden: list[str] = []
        tasks: dict[str, TaskExecutionRecord] = {}
        for task in plan.tasks:
            # C1: sin este chequeo, una Task duplicada dentro del mismo Plan
            # se perdía en silencio -- `tasks[task.id] = ...` sobrescribía el
            # registro anterior (perdiendo su `depende_de`) mientras `orden`
            # se quedaba con el id repetido dos veces, corrompiendo las colas
            # del snapshot y haciendo que `_actualizar_ready` intentara
            # transicionar la misma Task dos veces.
            if task.id in tasks:
                raise InvalidPlanError(f"El Plan {plan.id!r} contiene la Task {task.id!r} más de una vez.")
            orden.append(task.id)
            tasks[task.id] = TaskExecutionRecord(task_id=task.id, depende_de=tuple(task.depende_de))

        orden_t = tuple(orden)
        resolver.validar_grafo(tasks, orden_t)

        # Invariante FASE9 ("una Task pertenece a un único Plan"): antes esta
        # comprobación vivía dentro de InMemorySchedulerStore.crear(), que
        # lanzaba InvalidPlanError -- una decisión de dominio agazapada en lo
        # que debía ser persistencia pura. El Store ahora solo expone una
        # consulta de solo lectura; la decisión de rechazar o no vive aquí,
        # en el único punto de escritura del Manager.
        for task_id in tasks:
            plan_existente = self._store.plan_de_task_o_none(task_id)
            if plan_existente is not None and plan_existente != plan.id:
                raise InvalidPlanError(
                    f"La Task {task_id!r} ya pertenece al plan {plan_existente!r}; no puede repetirse en {plan.id!r}."
                )

        run = SchedulerRun(plan_id=plan.id, tasks=tasks, orden=orden_t)
        self._store.crear(run)
        run.registrar(events.SCHEDULER_CREADO, a=run.estado.value)
        self._events.emit(events.SCHEDULER_CREADO, plan_id=run.plan_id, total_tasks=len(tasks))

        self._actualizar_ready(run)
        self._store.guardar(run)
        return run

    def iniciar(self, plan_id: str) -> SchedulerRun:
        run = self._require(plan_id)
        return self._transicion_scheduler(run, "iniciar")

    def pausar(self, plan_id: str) -> SchedulerRun:
        run = self._require(plan_id)
        return self._transicion_scheduler(run, "pausar")

    def reanudar(self, plan_id: str) -> SchedulerRun:
        run = self._require(plan_id)
        return self._transicion_scheduler(run, "reanudar")

    def cancelar(self, plan_id: str) -> SchedulerRun:
        """SCHEDULER.md v1.0: toda Task que no esté ya COMPLETED, FAILED o
        CANCELLED (es decir PENDING, READY, BLOCKED o RUNNING) pasa a
        CANCELLED, vía la misma `_transicion_task` que usa cualquier otra
        transición de Task -- ya no un bloque de mutación manual aparte."""
        run = self._require(plan_id)
        terminales = (
            TaskExecutionState.COMPLETED,
            TaskExecutionState.FAILED,
            TaskExecutionState.CANCELLED,
        )
        cancelables = [tid for tid in run.orden if run.tasks[tid].estado not in terminales]
        for task_id in cancelables:
            self._transicion_task(run, run.tasks[task_id], TaskExecutionState.CANCELLED)

        return self._transicion_scheduler(run, "cancelar", tasks_cancelados=tuple(cancelables))

    def marcar_running(self, task_id: str) -> SchedulerRun:
        run, record = self._require_task(task_id)
        if run.estado is not SchedulerState.RUNNING:
            raise IllegalSchedulerTransitionError(run.estado.value, SchedulerState.RUNNING.value, contexto="scheduler")
        self._transicion_task(run, record, TaskExecutionState.RUNNING)
        self._store.guardar(run)
        return run

    def marcar_completed(self, task_id: str) -> SchedulerRun:
        run, record = self._require_task(task_id)
        if run.estado is not SchedulerState.RUNNING:
            raise IllegalSchedulerTransitionError(run.estado.value, SchedulerState.RUNNING.value, contexto="scheduler")
        self._transicion_task(run, record, TaskExecutionState.COMPLETED)
        self._actualizar_ready(run)
        self._store.guardar(run)
        return run

    def marcar_failed(self, task_id: str) -> SchedulerRun:
        run, record = self._require_task(task_id)
        if run.estado is not SchedulerState.RUNNING:
            raise IllegalSchedulerTransitionError(run.estado.value, SchedulerState.RUNNING.value, contexto="scheduler")
        self._transicion_task(run, record, TaskExecutionState.FAILED)

        for bloqueada_id in resolver.tasks_bloqueadas_por(run.tasks, task_id, run.orden):
            self._transicion_task(run, run.tasks[bloqueada_id], TaskExecutionState.BLOCKED)

        self._store.guardar(run)
        return run

    def ready_tasks(self, plan_id: str) -> list[str]:
        run = self._require(plan_id)
        return [tid for tid in run.orden if run.tasks[tid].estado is TaskExecutionState.READY]

    def snapshot(self, plan_id: str) -> SchedulerSnapshot:
        run = self._require(plan_id)

        def cola(estado: TaskExecutionState) -> tuple[str, ...]:
            return tuple(tid for tid in run.orden if run.tasks[tid].estado is estado)

        return SchedulerSnapshot(
            plan_id=run.plan_id,
            estado=run.estado,
            cola_ready=cola(TaskExecutionState.READY),
            cola_pending=cola(TaskExecutionState.PENDING),
            cola_running=cola(TaskExecutionState.RUNNING),
            cola_completed=cola(TaskExecutionState.COMPLETED),
            cola_failed=cola(TaskExecutionState.FAILED),
            creado_en=run.creado_en,
            actualizado_en=run.actualizado_en,
        )
