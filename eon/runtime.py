"""
eon.runtime
================
Runtime de integración mínimo para ejecutar el Kernel EON de punta a punta.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coordinator import CoordinatorManager, FileCoordinatorStore
from .coordinator.events import SCHEDULER_FINALIZADO
from .coordinator.exceptions import RetriesExhaustedError
from .coordinator.ports import ObjectivesPort, PlannerPort, TaskGenerationPort, VerifierPort, WorkspacePort
from .event_bus import EventBus
from .governance import (
    AuditLog,
    ExecutionContext,
    PolicyDecision,
    PolicyEngine,
)
from .governance import events as governance_events
from .objectives import InMemoryObjectiveStore, ObjectiveManager
from .objectives.exceptions import RetryLimitExceededError
from .objectives.verifier import Verifier
from .package import InMemoryPackageStore, PackageManager
from .package.models import WorkspaceRef
from .planner import InMemoryPlannerStore, PlannerManager
from .planner.task import Task
from .scheduler import InMemorySchedulerStore, SchedulerManager
from .scheduler import events as scheduler_events
from .scheduler.models import SchedulerState, TaskExecutionState
from .task_generation import DeterministicTaskGenerator, TaskSpec
from .workers import Dispatcher, InMemoryWorkerStore, TaskExecutor, WorkerManager, WorkerRegistry
from .workers import events as worker_events
from .workspace import InMemoryWorkspaceStore, WorkspaceManager


class PersistentEventBus(EventBus):
    """EventBus que persiste cada evento al EventStore antes de notificar
    subscribers. Implementa el outbox pattern: si el proceso muere tras
    persistir pero antes de notificar, al reiniciar se puede replay."""

    def __init__(self, event_store: Any) -> None:
        super().__init__()
        self._event_store = event_store

    def emit(self, event_name: str, **data: Any) -> None:
        # Persistir antes de notificar (outbox).
        execution_id = data.get("execution_id") or data.get("objective_id") or data.get("plan_id")
        try:
            self._event_store.append(
                execution_id=str(execution_id) if execution_id else None,
                event_type=event_name,
                payload=dict(data),
            )
        except Exception:
            logger = __import__("logging").getLogger("eon.runtime")
            logger.exception("Error persistiendo evento %s al EventStore", event_name)
        # Notificar subscribers (comportamiento normal del EventBus).
        super().emit(event_name, **data)


@dataclass(frozen=True)
class KernelRunResult:
    execution_id: str
    package_id: str
    package_state: str


def _task_spec_a_task(spec: Any) -> Task:
    """Fase 2 (Task Generation): convierte un `TaskSpec`
    (`eon.task_generation.models`) en el `Task` real que exige
    `PlannerManager` (`eon.planner.task`). Esta conversión pertenece al lado
    del Planner -- aquí, en el adaptador -- nunca al Coordinator ni a quien
    genera la especificación."""
    return Task(
        capability_id=spec.capability_id,
        id=spec.id,
        depende_de=tuple(spec.depende_de),
        parametros=dict(spec.parametros),
    )


@dataclass(frozen=True)
class VerifierPortResultado:
    """Vista de solo lectura devuelta por `VerifierPortAdapter`, tal como la
    exige `eon.coordinator.ports.VerifierPort` (`dictamen`, `confianza`,
    `justificacion`)."""

    dictamen: str
    confianza: float
    justificacion: str = ""


@dataclass(frozen=True)
class _ObjetivoParaVerificar:
    """Lo mínimo que `Verifier.verificar` necesita leer de un Objetivo
    (`criterio_de_exito`, `confianza_minima`) -- construido aquí porque
    `VerifierPort.verificar` recibe `criterio`/`umbral` sueltos, no un
    Objetivo completo (VERIFIER.md §9.1), y el Verifier real exige un
    objeto con esos dos atributos (OBJECTIVES.md §1)."""

    criterio_de_exito: str
    confianza_minima: float


class VerifierPortAdapter(VerifierPort):
    """Satisface `eon.coordinator.ports.VerifierPort` delegando en el
    Verifier real de dominio (`eon.objectives.verifier.Verifier`) -- Fase 3,
    Integración del Verifier real. Este adaptador NO decide ningún
    dictamen: solo traduce entre los dos contratos, en ambas direcciones:

    1. `evidence` (bruto, tal como lo produce el Scheduler/Workers) ->
       `(cumple_bruto, confianza)` ya evaluado. Sin un judge basado en LLM
       (fuera de alcance de esta fase -- Verifier nunca importa uno,
       Invariante 13), esta es la única entrada que el Verifier real acepta
       para decidir sin razonamiento de por medio (su propio docstring:
       "se acepta un (cumple, confianza) ya evaluado"). La `confianza` es
       la proporción real de Tasks completadas por el Scheduler, no un
       valor inventado por este adaptador.
    2. La `VerificationResult` (`cumple`/`confianza`/`motivo`) que devuelve
       `Verifier.verificar` -- que es quien de verdad decide `cumple`,
       incluida la comparación contra `confianza_minima` (VERIFIER.md,
       OBJECTIVES.md §9.2) -- se traduce a `VerifierPortResultado`
       (`dictamen`/`confianza`/`justificacion`), el contrato que el
       Coordinator espera.
    """

    def __init__(self, verifier: Verifier) -> None:
        self._verifier = verifier

    def verificar(self, evidence: Any, criterio: str, umbral: float) -> VerifierPortResultado:
        objetivo = _ObjetivoParaVerificar(criterio_de_exito=criterio, confianza_minima=umbral)
        resultado_bruto = self._resultado_desde_evidence(evidence)
        resultado = self._verifier.verificar(objetivo, resultado_bruto)
        dictamen = "aprobado" if resultado.cumple else "rechazado"
        return VerifierPortResultado(dictamen=dictamen, confianza=resultado.confianza, justificacion=resultado.motivo)

    @staticmethod
    def _resultado_desde_evidence(evidence: Any) -> tuple[bool, float]:
        """Traduce el `evidence` del Scheduler en `(cumple_bruto,
        confianza)`. Si `evidence` trae el desglose de Tasks
        (`tasks_totales`/`tasks_completadas`, ver
        `KernelRuntime._maybe_finalize_scheduler`), la confianza es la
        proporción real de Tasks completadas; si no, se limita a
        interpretar `evidence` como verdadero/falso."""
        if isinstance(evidence, dict):
            cumple = bool(evidence.get("ok", False))
            total = evidence.get("tasks_totales")
            completadas = evidence.get("tasks_completadas")
            if isinstance(total, int) and total > 0 and isinstance(completadas, int):
                confianza = completadas / total
            else:
                confianza = 1.0 if cumple else 0.0
        else:
            cumple = bool(evidence)
            confianza = 1.0 if cumple else 0.0
        return cumple, confianza


class ObjectivesPortAdapter(ObjectivesPort):
    """Satisface `eon.coordinator.ports.ObjectivesPort` delegando en el
    `ObjectiveManager` real de dominio (`eon.objectives.objective_manager`).

    El Coordinator está aislado por contrato de `eon.objectives`
    (`coordinator/ports.py`; `coordinator/tests/test_coordinator.py`
    `test_ningun_modulo_importa_paquetes_del_kernel_ajenos` lo hace
    cumplir en CI) -- por eso `CoordinatorManager` nunca puede importar
    `eon.objectives.exceptions.RetryLimitExceededError` para traducirla.
    Esa traducción vive aquí, en el adaptador de integración, igual que
    `VerifierPortAdapter` traduce entre los dos contratos de verificación:
    `ObjectivesPort.reintentar` (`ports.py`, docstring) promete lanzar
    `RetriesExhaustedError` cuando el Objetivo agotó su política de
    reintentos (OBJECTIVES.md §11) -- `ObjectiveManager.reintentar()`
    señaliza ese mismo evento de dominio con su propia excepción
    (`RetryLimitExceededError`), y este adaptador es quien une ambos
    contratos sin que ninguno de los dos lados conozca al otro."""

    def __init__(self, objectives: ObjectiveManager) -> None:
        self._objectives = objectives

    def crear_objetivo(self, descripcion: str, criterio_de_exito: str, **kwargs: Any) -> Any:
        return self._objectives.crear_objetivo(descripcion, criterio_de_exito, **kwargs)

    def planificar(self, objective_id: str, motivo: str | None = None) -> Any:
        return self._objectives.planificar(objective_id, motivo)

    def iniciar(self, objective_id: str) -> Any:
        return self._objectives.iniciar(objective_id)

    def obtener(self, objective_id: str) -> Any:
        return self._objectives.obtener(objective_id)

    def verificar(self, objective_id: str, resultado: Any) -> Any:
        return self._objectives.verificar(objective_id, resultado)

    def reintentar(self, objective_id: str) -> Any:
        try:
            return self._objectives.reintentar(objective_id)
        except RetryLimitExceededError as agotado:
            raise RetriesExhaustedError(objective_id) from agotado

    def cancelar(self, objective_id: str, motivo: str) -> Any:
        return self._objectives.cancelar(objective_id, motivo)


class PlannerPortAdapter(PlannerPort):
    """Satisface `eon.coordinator.ports.PlannerPort`. Recibe `TaskSpec[]`
    (ya generados por `TaskGenerationPort` antes de llegar aquí) y es quien
    los convierte a `Task` real para invocar `PlannerManager` -- el
    Coordinator nunca ve un `Task` ni un `TaskSpec` convertido."""

    def __init__(self, planner: PlannerManager) -> None:
        self._planner = planner
        self._plans: dict[str, Any] = {}

    def planificar(self, objective_id: str, tasks: Any) -> Any:
        plan = self._planner.crear_plan(objective_id, [_task_spec_a_task(t) for t in tasks])
        self._plans[plan.id] = plan
        return plan

    def replanificar(self, objective_id: str, motivo: str, tasks: Any) -> Any:
        plan = self._planner.replanificar(objective_id, [_task_spec_a_task(t) for t in tasks], motivo=motivo)
        self._plans[plan.id] = plan
        return plan

    def get_plan(self, plan_id: str) -> Any:
        return self._plans[plan_id]


class SchedulerPortAdapter:
    def __init__(self, scheduler: SchedulerManager, planner_adapter: PlannerPortAdapter) -> None:
        self._scheduler = scheduler
        self._planner_adapter = planner_adapter

    def crear(self, plan_id: str, **kwargs: Any) -> str:
        plan = self._planner_adapter.get_plan(plan_id)
        self._scheduler.crear(plan)
        return plan.id

    def iniciar(self, scheduler_id: str) -> None:
        self._scheduler.iniciar(scheduler_id)

    def cancelar(self, scheduler_id: str, motivo: str) -> None:
        self._scheduler.cancelar(scheduler_id, motivo)


class WorkspacePortAdapter(WorkspacePort):
    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self._workspace = workspace_manager

    def crear(self, objective_id: str, configuracion: dict | None = None) -> str:
        ws = self._workspace.crear(objective_id, configuracion)
        return ws.id

    def planificando(self, workspace_id: str) -> None:
        self._workspace.planificar(workspace_id)

    def scheduling(self, workspace_id: str) -> None:
        self._workspace.programar(workspace_id)

    def ejecutando(self, workspace_id: str) -> None:
        self._workspace.ejecutar(workspace_id)

    def verificando(self, workspace_id: str) -> None:
        self._workspace.verificar(workspace_id)

    def completar(self, workspace_id: str) -> None:
        self._workspace.completar(workspace_id)

    def fallar(self, workspace_id: str, motivo: str) -> None:
        self._workspace.fallar(workspace_id, motivo)

    def cancelar(self, workspace_id: str, motivo: str) -> None:
        self._workspace.cancelar(workspace_id, motivo)

    def obtener(self, workspace_id: str) -> WorkspaceRef:
        ws = self._workspace.obtener(workspace_id)
        artifacts_path = str(self._workspace._layout(workspace_id).resolver("artifacts"))
        return WorkspaceRef(
            workspace_id=ws.id,
            objective_id=ws.objective_id,
            estado=ws.estado.value,
            artifacts_path=artifacts_path,
        )


class _ExplicitTaskGenerator(TaskGenerationPort):
    """Satisface `TaskGenerationPort` a partir de una lista de `Task` fija,
    dada explícitamente a `KernelRuntime(tasks=...)` -- compatibilidad con
    el uso anterior a la Fase 2 (Task Generation), donde el llamador podía
    entregar sus propias Tasks. Cada llamada a `generar` (planificación
    inicial y cada replanificación) clona la lista original con ids nuevos,
    igual que hacía el `task_supplier` previo a esta fase."""

    def __init__(self, tasks: list[Task]) -> None:
        self._original_tasks = list(tasks)

    def generar(self, objective: Any) -> list[TaskSpec]:
        id_map = {task.id: str(uuid.uuid4()) for task in self._original_tasks}
        especificaciones: list[TaskSpec] = []
        for task in self._original_tasks:
            depende_de = tuple(id_map[dep] for dep in task.depende_de)
            especificaciones.append(
                TaskSpec(
                    capability_id=task.capability_id,
                    id=id_map[task.id],
                    depende_de=depende_de,
                    parametros=dict(task.parametros),
                )
            )
        return especificaciones


class KernelRuntime:
    def __init__(
        self,
        root: str | Path | None = None,
        tasks: list[Task] | None = None,
        task_executor: Callable[[str, dict], bool] | None = None,
        max_reintentos: int = 2,
        verifier: VerifierPort | None = None,
        task_generation: TaskGenerationPort | None = None,
        worker_capabilities: list[str] | None = None,
        policy_engine: PolicyEngine | None = None,
        audit_log: AuditLog | None = None,
        execution_context: ExecutionContext | None = None,
        persistence_backend: str | None = None,
        db_path: str | None = None,
        checkpointing_enabled: bool = False,
        checkpoint_policy: str = "manual",
    ) -> None:
        """Fase 1 (Persistencia): si `persistence_backend` es "sqlite",
        todos los stores usan SQLite (durable, sobrevive reinicios). Si es
        None o "memory", se usa InMemory (comportamiento anterior).

        Fase 3 (Checkpointing): si `checkpointing_enabled` es True, se activa
        la creación automática de checkpoints según `checkpoint_policy`.
        """
        self.root = Path(root or ".eon_runtime").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._worker_capabilities = worker_capabilities

        # Fase 1: seleccionar backend de persistencia.
        self._store_registry = None
        if persistence_backend == "sqlite":
            from .persistence import create_stores

            resolved_db = db_path or str(self.root / "eon.db")
            self._store_registry = create_stores(backend="sqlite", db_path=resolved_db)
            self._event_bus = PersistentEventBus(self._store_registry.event_store)
            self._coordinator_store = self._store_registry.coordinator_store
            self._objective_store = self._store_registry.objective_store
            self._planner_store = self._store_registry.planner_store
            self._scheduler_store = self._store_registry.scheduler_store
            self._workspace_store = self._store_registry.workspace_store
            self._package_store = self._store_registry.package_store
            self._worker_store = self._store_registry.worker_store
        else:
            self._event_bus = EventBus()
            self._coordinator_store = FileCoordinatorStore(self.root / "coordinator")
            self._objective_store = InMemoryObjectiveStore()
            self._planner_store = InMemoryPlannerStore()
            self._scheduler_store = InMemorySchedulerStore()
            self._workspace_store = InMemoryWorkspaceStore()
            self._package_store = InMemoryPackageStore()
            self._worker_store = InMemoryWorkerStore()

        self._workspace_manager = WorkspaceManager(self.root / "workspace", self._workspace_store, self._event_bus)
        self._package_manager = PackageManager(self.root / "package", self._package_store, self._event_bus)
        self._objective_manager = ObjectiveManager(
            self._objective_store, self._event_bus, max_reintentos=max_reintentos
        )
        self._planner_manager = PlannerManager(self._planner_store, self._event_bus)
        self._scheduler_manager = SchedulerManager(self._scheduler_store, self._event_bus)
        self._worker_manager = WorkerManager(self._worker_store, self._event_bus)
        self._worker_registry = WorkerRegistry(self._worker_store)
        self._task_executor = TaskExecutor(self._event_bus, ejecutar=task_executor)
        self._dispatcher = Dispatcher(self._worker_registry, self._worker_manager, self._task_executor)

        # Fase 3: CheckpointManager (solo si hay SQLite)
        self._checkpoint_manager = None
        if self._store_registry is not None and self._store_registry.engine is not None:
            from .checkpoint import CheckpointManager, SQLiteCheckpointStore

            cp_store = SQLiteCheckpointStore(self._store_registry.engine)
            self._checkpoint_manager = CheckpointManager(
                checkpoint_store=cp_store,
                event_store=self._store_registry.event_store,
                objective_store=self._objective_store,
                planner_store=self._planner_store,
                scheduler_store=self._scheduler_store,
                workspace_store=self._workspace_store,
                package_store=self._package_store,
                worker_store=self._worker_store,
                coordinator_store=self._coordinator_store,
            )
            if checkpointing_enabled:
                self._checkpoint_manager.set_auto_policy(checkpoint_policy)

        # Fase 3.5: HITL Manager + Tracer (solo si hay SQLite)
        self._hitl_manager = None
        self._tracer = None
        self._time_machine = None
        self._fork_manager = None
        if self._store_registry is not None and self._store_registry.engine is not None:
            from .hitl import HITLManager, SQLiteHITLStore
            from .tracing import SQLiteTraceStore, Tracer

            hitl_store = SQLiteHITLStore(self._store_registry.engine)
            self._hitl_manager = HITLManager(
                hitl_store=hitl_store,
                checkpoint_manager=self._checkpoint_manager,
                event_store=self._store_registry.event_store,
            )

            trace_store = SQLiteTraceStore(self._store_registry.engine)
            self._tracer = Tracer(trace_store)

            # Fase 4: TimeMachine (solo si hay checkpoint_store + event_store)
            if self._checkpoint_manager is not None:
                from .timetravel import TimeMachine

                self._time_machine = TimeMachine(
                    checkpoint_store=cp_store,
                    event_store=self._store_registry.event_store,
                )

            # Fase 5: ForkManager
            from .forking import ForkManager, SQLiteForkStore

            fork_store = SQLiteForkStore(self._store_registry.engine)
            self._fork_manager = ForkManager(
                fork_store=fork_store,
                checkpoint_store=cp_store,
                event_store=self._store_registry.event_store,
            )

        self._planner_adapter = PlannerPortAdapter(self._planner_manager)
        self._workspace_adapter = WorkspacePortAdapter(self._workspace_manager)
        self._scheduler_adapter = SchedulerPortAdapter(self._scheduler_manager, self._planner_adapter)
        self._objectives_adapter = ObjectivesPortAdapter(self._objective_manager)
        # Fase 3 (Integración del Verifier real): por defecto se usa el
        # Verifier real de dominio (`eon.objectives.verifier.Verifier`),
        # nunca un fake -- `verifier=` solo existe para que un test pueda
        # inyectar explícitamente un doble de prueba (`eon.testing`).
        self._verifier = verifier if verifier is not None else VerifierPortAdapter(Verifier())
        # Fase 15 (Autonomous Task Generation / Planning Intelligence): si el
        # llamador entrega un `task_generation` (típicamente
        # `ValidatingTaskGenerator(LLMTaskGenerator(...), TaskSpecValidator(...))`,
        # ver `eon/__main__.py --use-llm-task-generation`), se usa tal cual --
        # el runtime nunca decide por su cuenta si gobernar o no una
        # generación basada en LLM, solo respeta lo que se le inyecta. Sin
        # `task_generation=`, se conserva el comportamiento de la Fase 2
        # (`DeterministicTaskGenerator`/`_ExplicitTaskGenerator`).
        self._task_generation = task_generation or self._make_task_generation_port(tasks)
        self.coordinator = CoordinatorManager(
            str(self.root / "coordinator"),
            self._coordinator_store,
            self._event_bus,
            self._objectives_adapter,
            self._planner_adapter,
            self._scheduler_adapter,
            self._verifier,
            self._workspace_adapter,
            self._package_manager,
            self._task_generation,
        )

        self._register_default_workers(tasks)
        self._subscribe_events()
        self._finalized_plans: set[str] = set()
        # Bugfix Fase 6 (Worker Lifecycle & Resource Management): ver
        # `_on_task_ready`/`_drain_dispatch_queue`.
        self._dispatch_queue: list[Task] = []
        self._dispatching = False
        # Fase 3 (Gobernanza): el PolicyEngine es la última compuerta antes
        # de despachar al Worker. Si es ``None``, el runtime se comporta
        # exactamente igual que antes — gobernanza es opt-in.
        self._policy_engine = policy_engine
        self._audit_log = audit_log or AuditLog()
        self._execution_context = execution_context or ExecutionContext()
        self._current_execution_id: str | None = None

    @property
    def workspace_manager(self) -> WorkspaceManager:
        return self._workspace_manager

    @property
    def package_manager(self) -> PackageManager:
        return self._package_manager

    @property
    def history_store(self) -> FileCoordinatorStore:
        return self._coordinator_store

    @property
    def policy_engine(self) -> PolicyEngine | None:
        return self._policy_engine

    @property
    def audit_log(self) -> AuditLog:
        return self._audit_log

    def _make_task_generation_port(self, tasks: list[Task] | None) -> TaskGenerationPort:
        """Fase 2 (Task Generation): si el llamador entregó `tasks`
        explícitamente, se respetan (compatibilidad con el uso anterior a
        esta fase). Si no, `DeterministicTaskGenerator` genera la
        especificación de trabajo a partir del Objective -- así el criterio
        de aceptación de la Fase 2 (iniciar una ejecución con solo
        `descripcion`/`criterio_de_exito`, sin Tasks manuales) queda
        cubierto por defecto."""
        if tasks is None:
            return DeterministicTaskGenerator()
        return _ExplicitTaskGenerator(tasks)

    def _register_default_workers(self, tasks: list[Task] | None) -> None:
        if self._worker_capabilities is not None:
            # Fase 15: con un `task_generation` que razona (típicamente
            # LLM-backed), las capabilities de las Tasks no se conocen hasta
            # ejecutar -- se registran todas las que el llamador declaró
            # como válidas (normalmente las mismas que se le pasaron a
            # `TaskSpecValidator`/`LLMTaskGenerator`), no solo `{"default"}`.
            capabilities = set(self._worker_capabilities) or {"default"}
        else:
            capabilities = {"default"} if not tasks else {task.capability_id for task in tasks}
        for capability_id in sorted(capabilities):
            self._worker_manager.registrar([capability_id], nombre=f"worker-{capability_id}")

    def _subscribe_events(self) -> None:
        self._event_bus.subscribe(scheduler_events.SCHEDULER_INICIADO, self._on_scheduler_started)
        self._event_bus.subscribe(scheduler_events.TASK_READY, self._on_task_ready)
        self._event_bus.subscribe(worker_events.TASK_COMPLETADA, self._on_task_completed)
        self._event_bus.subscribe(worker_events.TASK_FALLIDA, self._on_task_failed)

    def _on_scheduler_started(self, plan_id: str, **_: Any) -> None:
        for task_id in self._scheduler_manager.ready_tasks(plan_id):
            self._on_task_ready(task_id=task_id, plan_id=plan_id)

    def _on_task_ready(self, task_id: str, plan_id: str, **_: Any) -> None:
        run = self._scheduler_store.obtener(plan_id)
        if run.estado is not SchedulerState.RUNNING:
            return

        plan = self._planner_adapter.get_plan(plan_id)
        task = next((task for task in plan.tasks if task.id == task_id), None)
        if task is None:
            return
        self._scheduler_manager.marcar_running(task_id)
        self._dispatch_queue.append(task)
        self._drain_dispatch_queue()

    def _drain_dispatch_queue(self) -> None:
        """Bugfix Fase 6 (Worker Lifecycle & Resource Management):
        `Dispatcher.despachar()` no libera el Worker (`WorkerManager.liberar`)
        hasta que `TaskExecutor.ejecutar()` retorna -- pero `ejecutar()` ya
        emitió `task_completada`/`task_fallida` (síncrono, `EventBus.emit`
        no encola) antes de retornar, y reaccionar a ese evento aquí mismo
        puede desencadenar, en el mismo hilo de llamada, una
        replanificación que vuelve a pedir Tasks READY para el mismo
        capability_id (`_on_scheduler_started` -> `_on_task_ready`) --
        *antes* de que el `despachar()` original, más abajo en la pila,
        llegue a liberar su Worker. Sin este encolado, ese segundo
        `despachar()` anidado encontraría el único Worker todavía BUSY y
        fallaría con `NoCompatibleWorkerError`, obligando artificialmente a
        registrar un segundo Worker para que el flujo funcione.

        Encolar y procesar solo desde la invocación más externa (guardada
        por `_dispatching`) asegura que cada `despachar()` se ejecute de
        principio a fin -- incluida su liberación del Worker -- antes de
        que se intente el siguiente, sin importar cuántos niveles de
        replanificación se disparen síncronamente en la reacción a sus
        eventos."""
        if self._dispatching:
            return
        self._dispatching = True
        try:
            while self._dispatch_queue:
                tarea = self._dispatch_queue.pop(0)
                # Fase 3 (Gobernanza): el PolicyEngine evalúa cada Task
                # antes de despacharla al Worker. Si es ``None``, se
                # despacha directamente — comportamiento anterior.
                if self._policy_engine is not None:
                    result = self._policy_engine.decidir(tarea, context=self._execution_context)
                    exec_id = self._current_execution_id or "unknown"
                    # Auditar la decisión (siempre, permitida o denegada)
                    self._audit_log.registrar(
                        execution_id=exec_id,
                        event_type=governance_events.POLICY_EVALUATED,
                        decision=result.decision.value,
                        policy_id=result.policy_id,
                        reason=result.reason,
                        task_id=tarea.id,
                        capability_id=tarea.capability_id,
                        sandbox_profile=(result.sandbox_profile.profile_id if result.sandbox_profile else None),
                    )
                    self._event_bus.emit(
                        governance_events.POLICY_EVALUATED,
                        task_id=tarea.id,
                        capability_id=tarea.capability_id,
                        decision=result.decision.value,
                        policy_id=result.policy_id,
                        reason=result.reason,
                    )
                    if result.decision is PolicyDecision.DENY:
                        # Denegada: marcar como failed sin despachar
                        self._scheduler_manager.marcar_failed(tarea.id)
                        self._event_bus.emit(
                            governance_events.POLICY_DENIED,
                            task_id=tarea.id,
                            capability_id=tarea.capability_id,
                            policy_id=result.policy_id,
                            reason=result.reason,
                        )
                        self._maybe_finalize_scheduler(tarea.id)
                        continue
                    if result.decision is PolicyDecision.REQUIRES_APPROVAL:
                        # Para este sprint: tratarse como DENY controlado.
                        # La aprobación humana completa merece un sprint
                        # separado — no se deja la Task colgada en RUNNING.
                        self._scheduler_manager.marcar_failed(tarea.id)
                        self._event_bus.emit(
                            governance_events.APPROVAL_REQUESTED,
                            task_id=tarea.id,
                            capability_id=tarea.capability_id,
                            policy_id=result.policy_id,
                            reason=result.reason,
                        )
                        self._maybe_finalize_scheduler(tarea.id)
                        continue
                    # ALLOW: emitir sandbox_applied si hay perfil
                    if result.sandbox_profile is not None:
                        self._event_bus.emit(
                            governance_events.SANDBOX_APPLIED,
                            task_id=tarea.id,
                            profile_id=result.sandbox_profile.profile_id,
                        )
                self._dispatcher.despachar(tarea)
        finally:
            self._dispatching = False

    def _on_task_completed(self, worker_id: str, task_id: str, **_: Any) -> None:
        self._write_artifact(worker_id, task_id)
        self._scheduler_manager.marcar_completed(task_id)
        self._maybe_finalize_scheduler(task_id)

    def _on_task_failed(self, worker_id: str, task_id: str, **_: Any) -> None:
        self._scheduler_manager.marcar_failed(task_id)
        self._maybe_finalize_scheduler(task_id)

    def _write_artifact(self, worker_id: str, task_id: str) -> None:
        registry = self._workspace_manager.artefactos(self._latest_workspace_id())
        registry.escribir(
            worker_id, f"{task_id}.txt", json.dumps({"task_id": task_id, "worker_id": worker_id}).encode("utf-8")
        )
        self._workspace_manager.sincronizar_artefactos(self._latest_workspace_id(), registry)

    def _latest_workspace_id(self) -> str:
        workspaces = self._workspace_store.list()
        if not workspaces:
            raise RuntimeError("No workspace was created yet.")
        return workspaces[-1].id

    def _maybe_finalize_scheduler(self, task_id: str) -> None:
        plan_id = self._scheduler_store.plan_de_task(task_id)
        if plan_id in self._finalized_plans:
            return
        run = self._scheduler_store.obtener(plan_id)
        if run.estado is not SchedulerState.RUNNING:
            return
        if any(r.estado is TaskExecutionState.READY for r in run.tasks.values()):
            return
        if any(r.estado is TaskExecutionState.RUNNING for r in run.tasks.values()):
            return
        if not all(record.estado.es_terminal for record in run.tasks.values()):
            return

        self._finalized_plans.add(plan_id)
        total = len(run.tasks)
        completadas = sum(1 for r in run.tasks.values() if r.estado is TaskExecutionState.COMPLETED)
        evidence = {
            "ok": completadas == total,
            "tasks_totales": total,
            "tasks_completadas": completadas,
        }
        self._event_bus.emit(SCHEDULER_FINALIZADO, scheduler_id=plan_id, evidence=evidence)

    def run(
        self,
        descripcion: str,
        criterio_de_exito: str,
        configuracion: dict | None = None,
        execution_id: str | None = None,
    ) -> KernelRunResult:
        """`execution_id`: CONSOLE.md §6 -- permite a un llamador (hoy, el
        backend de la Console vía subprocess) generar el id ANTES de
        lanzar esta llamada y devolverlo de inmediato al navegador, sin
        esperar a que `.run()` termine. Por defecto `None`: mismo
        comportamiento que siempre (`CoordinatorManager.iniciar_ejecucion`
        autogenera el id), ningún llamador existente se ve afectado."""
        # Fase 3: registrar el execution_id ANTES de iniciar la
        # ejecución, porque `iniciar_ejecucion()` despacha Tasks
        # sincrónicamente y el PolicyEngine necesita el ID para auditar.
        if execution_id is None:
            execution_id = str(uuid.uuid4())
        self._current_execution_id = execution_id
        execution = self.coordinator.iniciar_ejecucion(
            descripcion, criterio_de_exito, configuracion=configuracion, execution_id=execution_id
        )
        execution = self.coordinator.obtener(execution.id)
        if not execution.package_id:
            raise RuntimeError(f"Coordinator execution {execution.id} did not produce a package")
        package = self._package_manager.obtener(execution.package_id)
        return KernelRunResult(
            execution_id=execution.id,
            package_id=package.id,
            package_state=package.estado.value,
        )

    # ─── Fase 3: Checkpointing ───────────────────────────────

    def crear_checkpoint(
        self,
        execution_id: str,
        reason: str = "",
    ) -> Any | None:
        """Crea un checkpoint del estado actual de una ejecución.

        Captura estado técnico de los 7 stores + SemanticSnapshot.
        Anclado al EventStore via event_seq.

        Returns:
            Checkpoint creado, o None si el checkpointing no está disponible.
        """
        if self._checkpoint_manager is None:
            return None
        from .checkpoint.models import CheckpointKind

        return self._checkpoint_manager.crear_checkpoint(
            execution_id,
            reason=reason,
            kind=CheckpointKind.MANUAL,
        )

    def obtener_checkpoint(self, checkpoint_id: str) -> Any | None:
        """Obtiene un checkpoint por ID."""
        if self._checkpoint_manager is None:
            return None
        return self._checkpoint_manager.obtener_checkpoint(checkpoint_id)

    def ultimo_checkpoint(self, execution_id: str) -> Any | None:
        """Obtiene el checkpoint más reciente de una ejecución."""
        if self._checkpoint_manager is None:
            return None
        return self._checkpoint_manager.ultimo_checkpoint(execution_id)

    def listar_checkpoints(self, execution_id: str) -> list[Any]:
        """Lista todos los checkpoints de una ejecución."""
        if self._checkpoint_manager is None:
            return []
        return self._checkpoint_manager.listar_checkpoints(execution_id)

    def recuperar_desde_checkpoint(self, checkpoint_id: str) -> dict | None:
        """Recupera el estado desde un checkpoint (read-only).

        Devuelve un dict con:
        - checkpoint: el checkpoint completo
        - stores_state: estado de los stores en ese momento
        - semantic: semantic snapshot
        - events_after: eventos del EventStore posteriores
        - hash_verified: si el hash es válido
        """
        if self._checkpoint_manager is None:
            return None
        return self._checkpoint_manager.recuperar_desde_checkpoint(checkpoint_id)

    # ─── Fase 3.5: HITL Interrupt/Resume ────────────────────

    def crear_interrupcion_hitl(
        self,
        execution_id: str,
        task_id: str = "",
        tool_name: str = "",
        reason: str = "",
        payload: dict | None = None,
    ) -> Any | None:
        """Crea una interrupción HITL persistente para aprobación humana.

        Si hay checkpointing disponible, crea un checkpoint PRE_INTERRUPT
        y lo vincula a la interrupción.
        """
        if self._hitl_manager is None:
            return None
        return self._hitl_manager.crear_interrupcion(
            execution_id=execution_id,
            task_id=task_id,
            tool_name=tool_name,
            reason=reason,
            payload=payload,
        )

    def listar_pendientes_hitl(self) -> list[Any]:
        """Lista todas las interrupciones HITL pendientes."""
        if self._hitl_manager is None:
            return []
        return self._hitl_manager.listar_pendientes()

    def listar_interrupciones_hitl(self, execution_id: str) -> list[Any]:
        """Lista todas las interrupciones HITL de una ejecución."""
        if self._hitl_manager is None:
            return []
        return self._hitl_manager.listar_por_ejecucion(execution_id)

    def aprobar_interrupcion(
        self,
        interrupt_id: str,
        decided_by: str = "human",
        decision_reason: str = "",
    ) -> Any | None:
        """Aprueba una interrupción HITL: PENDING → APPROVED."""
        if self._hitl_manager is None:
            return None
        return self._hitl_manager.aprobar(interrupt_id, decided_by, decision_reason)

    def denegar_interrupcion(
        self,
        interrupt_id: str,
        decided_by: str = "human",
        decision_reason: str = "",
    ) -> Any | None:
        """Deniega una interrupción HITL: PENDING → DENIED."""
        if self._hitl_manager is None:
            return None
        return self._hitl_manager.denegar(interrupt_id, decided_by, decision_reason)

    def reanudar_desde_interrupcion(self, interrupt_id: str) -> dict | None:
        """Reanuda una ejecución desde una interrupción aprobada.

        APPROVED → RESUMED. Devuelve contexto recuperable con checkpoint.
        """
        if self._hitl_manager is None:
            return None
        return self._hitl_manager.reanudar(interrupt_id)

    # ─── Fase 3.5: Tracing ──────────────────────────────────

    def obtener_trace(self, execution_id: str) -> list[Any]:
        """Obtiene todos los spans de tracing de una ejecución."""
        if self._tracer is None:
            return []

        # Acceder al store via el tracer
        if hasattr(self._tracer, "_store") and self._tracer._store is not None:
            return self._tracer._store.list_spans(execution_id)
        return []

    def listar_spans(self, execution_id: str) -> list[Any]:
        """Alias para obtener_trace."""
        return self.obtener_trace(execution_id)

    # ─── Fase 4: Time Travel ─────────────────────────────────

    def inspeccionar_en(
        self,
        execution_id: str,
        event_seq: int,
    ) -> Any | None:
        """Reconstruye el estado del kernel en un event_seq específico.

        Usa el checkpoint más cercano con event_seq <= target_seq como base
        y adjunta el timeline de eventos posteriores hasta target_seq.

        La reconstrucción es read-only: no muta stores reales.

        Args:
            execution_id: ID de la ejecución a inspeccionar.
            event_seq: Número de secuencia del EventStore hasta donde
                reconstruir (inclusive).

        Returns:
            ReconstructedState con el estado en ese punto temporal,
            o None si TimeMachine no está disponible.
        """
        if self._time_machine is None:
            return None
        return self._time_machine.reconstruct_state(execution_id, event_seq)

    def obtener_timeline(
        self,
        execution_id: str,
        from_seq: int = 0,
        to_seq: int | None = None,
    ) -> list[dict]:
        """Devuelve el timeline de eventos de una ejecución.

        Args:
            execution_id: ID de la ejecución.
            from_seq: event_seq inicial (exclusive). Default 0.
            to_seq: event_seq final (inclusive). None = hasta el último.

        Returns:
            Lista de eventos como dicts, ordenados por seq ascendente.
        """
        if self._time_machine is None:
            return []
        return self._time_machine.get_timeline(execution_id, from_seq, to_seq)

    # ─── Fase 5: Execution Forking ──────────────────────────

    def fork_from_checkpoint(
        self,
        checkpoint_id: str,
        new_objective: str | None = None,
        metadata: dict | None = None,
    ) -> Any | None:
        """Crea un fork de ejecución desde un checkpoint.

        La ejecución forked hereda el estado del checkpoint pero diverge
        desde ahí. La ejecución original no se muta.

        Args:
            checkpoint_id: ID del checkpoint base.
            new_objective: Objetivo opcional modificado para el fork.
            metadata: Metadatos adicionales.

        Returns:
            ExecutionFork con los IDs de la nueva ejecución,
            o None si ForkManager no está disponible.
        """
        if self._fork_manager is None:
            return None
        return self._fork_manager.fork_from_checkpoint(
            checkpoint_id, new_objective, metadata
        )

    def obtener_fork(self, fork_id: str) -> Any | None:
        """Obtiene un fork por ID."""
        if self._fork_manager is None:
            return None
        return self._fork_manager.obtener_fork(fork_id)

    def listar_forks(self, execution_id: str) -> list[Any]:
        """Lista todos los forks de una ejecución parent."""
        if self._fork_manager is None:
            return []
        return self._fork_manager.listar_forks(execution_id)

    def comparar_forks(self, fork_id_a: str, fork_id_b: str) -> dict | None:
        """Compara dos forks read-only.

        Devuelve un dict con las diferencias entre los estados base
        de los dos forks.
        """
        if self._fork_manager is None:
            return None
        return self._fork_manager.comparar_forks(fork_id_a, fork_id_b)

    def obtener_fork_tree(self, execution_id: str) -> dict | None:
        """Construye el árbol de forks de una ejecución."""
        if self._fork_manager is None:
            return None
        return self._fork_manager.obtener_fork_tree(execution_id)

    def close(self) -> None:
        """Fase 1: cierra conexiones de persistencia si el backend es SQLite."""
        if self._store_registry is not None:
            self._store_registry.close()

    def __enter__(self) -> KernelRuntime:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ─── Fase 1: recuperación de ejecuciones ─────────────────────

    def recuperar_ejecucion(self, execution_id: str) -> Any:
        """Recupera el estado completo de una ejecución desde persistencia.

        Lee del CoordinatorStore y reconstruye el estado de todos los
        componentes asociados a esa ejecución: Objective, Plan, SchedulerRun,
        Workspace, Package, Workers.

        Args:
            execution_id: ID de la CoordinatorExecution a recuperar.

        Returns:
            CoordinatorExecution con su estado completo, o None si no existe.
        """
        execution = self._coordinator_store.get(execution_id)
        if execution is None:
            return None
        return execution

    def listar_ejecuciones(self) -> list[Any]:
        """Lista todas las ejecuciones del Coordinator."""
        return self._coordinator_store.list()

    def replay_events(self, execution_id: str | None = None) -> int:
        """Replay de eventos desde el EventStore.

        Lee eventos persistidos y los re-emite al EventBus para que los
        subscribers reconstruyan estado.

        Args:
            execution_id: Si se especifica, solo replay de esa ejecución.
            Si es None, replay global.

        Returns:
            Número de eventos replayed.
        """
        if self._store_registry is None or self._store_registry.event_store is None:
            return 0
        events = self._store_registry.event_store.get_events(
            execution_id=execution_id
        )
        for event in events:
            # Re-emitir al EventBus sin re-persistir (evitar loop).
            EventBus.emit(self._event_bus, event.event_type, **event.payload)
        return len(events)

    # ─── Fase 2: Runtime Asíncrono ────────────────────────────

    async def run_async(
        self,
        descripcion: str,
        criterio_de_exito: str,
        configuracion: dict | None = None,
        execution_id: str | None = None,
        max_workers: int | None = None,
        task_timeout: float = 30.0,
    ) -> KernelRunResult:
        """Ejecuta el kernel de forma asíncrona con workers concurrentes.

        Fase 2: usa AsyncEventBus + WorkerPool para ejecución paralela.
        La orquestación de Coordinator/Planner/Scheduler sigue siendo
        síncrona (como en run()), pero la ejecución de Tasks es concurrente.

        Args:
            max_workers: Número de workers concurrentes (default: cpu_count).
            task_timeout: Timeout por Task en segundos.

        Returns:
            KernelRunResult con el resultado de la ejecución.
        """

        from .workers.task_queue import SQLiteTaskQueue, TaskEntry
        from .workers.worker_pool import WorkerPool

        if execution_id is None:
            execution_id = str(uuid.uuid4())
        self._current_execution_id = execution_id

        # 1. Iniciar ejecución síncrona (CoordinatorManager es síncrono)
        execution = self.coordinator.iniciar_ejecucion(
            descripcion, criterio_de_exito, configuracion=configuracion, execution_id=execution_id
        )

        # 2. Si hay SQLite, usar WorkerPool para ejecución concurrente
        if self._store_registry is not None and self._store_registry.engine is not None:
            task_queue = SQLiteTaskQueue(self._store_registry.engine)

            # Encolar tasks PENDING/READY del scheduler
            scheduler_runs = self._scheduler_store.listar()
            for run in scheduler_runs:
                if run.estado.value != "running":
                    continue
                for task_id, record in run.tasks.items():
                    if record.estado.value in ("pending", "ready"):
                        task_queue.enqueue(
                            TaskEntry(
                                task_id=task_id,
                                capability_id=record.task_id,
                                payload={"plan_id": run.plan_id},
                                timeout_seconds=task_timeout,
                            )
                        )

            # Crear y arrancar WorkerPool
            pool = WorkerPool(
                queue=task_queue,
                event_bus=self._event_bus,
                executor=self._task_executor._ejecutar if hasattr(self._task_executor, "_ejecutar") else None,
                max_workers=max_workers,
            )
            await pool.start()
            try:
                await pool.wait_until_empty(timeout=300.0)
            finally:
                await pool.stop()

        # 3. Obtener resultado
        execution = self.coordinator.obtener(execution.id)
        if not execution.package_id:
            raise RuntimeError(f"Coordinator execution {execution.id} did not produce a package")
        package = self._package_manager.obtener(execution.package_id)
        return KernelRunResult(
            execution_id=execution.id,
            package_id=package.id,
            package_state=package.estado.value,
        )
