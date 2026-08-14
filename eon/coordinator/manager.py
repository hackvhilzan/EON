"""
eon.coordinator.manager
==========================
`CoordinatorManager` (ORDEN MAESTRA): el único componente autorizado para
orquestar el Kernel completo. No toma decisiones de dominio, no sustituye
a ningún módulo y no duplica responsabilidades -- únicamente coordina el
flujo entre los módulos ya existentes (TaskGeneration, Objectives, Planner,
Scheduler, Verifier, Workspace, Package) a través de sus interfaces públicas
(`ports.py`), respetando estrictamente el ORDEN DE EJECUCIÓN:

    Objective -> Planner -> Scheduler -> Workers -> Verifier ->
    Workspace -> Package -> Resultado Final

Interpretación de "EVENTOS" (ver informe, sección Ambigüedades): el
Coordinator delega llamando a las interfaces públicas de cada módulo (eso
es lo que exige la sección RESPONSABILIDADES -- "Siempre delegará") y,
además, todo avance de una fase a la siguiente queda mediado por el
`EventBus`: cada delegación termina emitiendo el evento de su propio
módulo, y es reaccionando a esos eventos (los suyos propios y los de los
módulos asíncronos -- Scheduler, Workspace) como el Coordinator decide el
siguiente paso, nunca por una llamada a función que devuelve control
directamente de una fase a la fase siguiente sin pasar por el bus.
"""

from __future__ import annotations

from typing import Any

from . import events
from .coordinator_store import CoordinatorStore
from .exceptions import (
    CoordinatorImmutableError,
    CoordinatorNotFoundError,
    CoordinatorRecoveryError,
    DelegationError,
    RetriesExhaustedError,
)
from .layout import CoordinatorLayout
from .models import NO_TERMINALES, CoordinatorExecution, CoordinatorState
from .ports import (
    EventBusPort,
    ObjectivesPort,
    PackagePort,
    PlannerPort,
    SchedulerPort,
    TaskGenerationPort,
    VerifierPort,
    WorkspacePort,
)
from .snapshot import CoordinatorSnapshot, cargar_snapshot, guardar_snapshot
from .validators import validar_transicion

_DICTAMEN_APROBADO = "aprobado"
_ESTADO_PACKAGE_READY = "ready"


class CoordinatorManager:
    def __init__(
        self,
        root: str,
        store: CoordinatorStore,
        event_bus: EventBusPort,
        objectives: ObjectivesPort,
        planner: PlannerPort,
        scheduler: SchedulerPort,
        verifier: VerifierPort,
        workspace: WorkspacePort,
        package: PackagePort,
        task_generation: TaskGenerationPort,
    ) -> None:
        self._root = root
        self._store = store
        self._events = event_bus
        self._objectives = objectives
        self._planner = planner
        self._scheduler = scheduler
        self._verifier = verifier
        self._workspace = workspace
        self._package = package
        self._task_generation = task_generation

        # ORDEN MAESTRA, "EVENTOS": el Coordinator reacciona a los eventos
        # asíncronos publicados por los módulos que no devuelven su
        # resultado final de forma síncrona (el bucle de despacho del
        # Scheduler, SCHEDULER.md §9.2; la finalización del Workspace).
        self._events.subscribe(events.SCHEDULER_FINALIZADO, self._on_scheduler_finalizado)
        self._events.subscribe(events.WORKSPACE_COMPLETADO, self._on_workspace_completado)
        self._events.subscribe(events.WORKSPACE_FALLIDO, self._on_workspace_fallido)

    # ---- infraestructura interna ----

    def _layout(self, execution_id: str) -> CoordinatorLayout:
        return CoordinatorLayout(self._root, execution_id)

    def _require(self, execution_id: str) -> CoordinatorExecution:
        execution = self._store.get(execution_id)
        if execution is None:
            raise CoordinatorNotFoundError(execution_id)
        return execution

    def _guardar_snapshot(self, execution: CoordinatorExecution) -> CoordinatorSnapshot:
        snap = CoordinatorSnapshot.desde_execution(execution)
        guardar_snapshot(self._layout(execution.id), snap)
        return snap

    def _persistir(self, execution: CoordinatorExecution) -> None:
        self._store.update(execution)
        self._guardar_snapshot(execution)

    def _transicionar(
        self, execution: CoordinatorExecution, destino: CoordinatorState, motivo: str | None = None
    ) -> None:
        origen = execution.estado
        evento = validar_transicion(origen, destino)
        execution.estado = destino
        execution.registrar_evento(evento, de=origen.value, a=destino.value, motivo=motivo)
        self._persistir(execution)
        self._events.emit(evento, execution_id=execution.id, de=origen.value, a=destino.value, motivo=motivo)

    def _delegar(self, modulo: str, operacion: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Único punto desde el que `CoordinatorManager` invoca una
        interfaz pública ajena. No añade lógica de dominio: solo traduce
        cualquier fallo del módulo delegado en `DelegationError`,
        preservando la causa (ver `exceptions.DelegationError`)."""
        try:
            return fn(*args, **kwargs)
        except RetriesExhaustedError:
            raise
        except Exception as causa:  # noqa: BLE001 -- traducción intencional, ver docstring
            raise DelegationError(modulo, operacion, causa) from causa

    def _fallar(self, execution: CoordinatorExecution, motivo: str) -> None:
        if execution.es_terminal():
            return
        self._transicionar(execution, CoordinatorState.FAILED, motivo=motivo)

    def _buscar_por_plan(self, plan_id: str) -> CoordinatorExecution | None:
        for execution in self._store.list():
            if execution.plan_id == plan_id and not execution.es_terminal():
                return execution
        return None

    def _buscar_por_workspace(self, workspace_id: str) -> CoordinatorExecution | None:
        for execution in self._store.list():
            if execution.workspace_id == workspace_id and not execution.es_terminal():
                return execution
        return None

    # ---- responsabilidad 1-4: Objective -> Planner -> Scheduler (síncrono) ----

    def iniciar_ejecucion(
        self,
        descripcion: str,
        criterio_de_exito: str,
        configuracion: dict | None = None,
        execution_id: str | None = None,
        **kwargs_objetivo: Any,
    ) -> CoordinatorExecution:
        """Punto de entrada único de una nueva ejecución. Cubre, en orden,
        las responsabilidades 1 a 3 de la ORDEN MAESTRA: crear el
        Objetivo, solicitar el Plan y lanzar el Scheduler. Las
        responsabilidades 4-9 (esperar finalización, verificar, gestionar
        replanificaciones, esperar Workspace COMPLETED, solicitar el
        Package, finalizar) ocurren en los manejadores de evento
        (`_on_scheduler_finalizado`, `_on_workspace_completado`), nunca
        aquí, porque son asíncronas respecto al llamador."""
        kwargs: dict[str, Any] = {"id": execution_id} if execution_id else {}
        execution = CoordinatorExecution(**kwargs)
        execution.registrar_evento(events.EJECUCION_CREADA, de=None, a=execution.estado.value)
        self._layout(execution.id).materializar()
        self._store.create(execution)
        self._guardar_snapshot(execution)
        self._events.emit(events.EJECUCION_CREADA, execution_id=execution.id)

        try:
            # Responsabilidad 1: crear el Objetivo.
            self._transicionar(execution, CoordinatorState.CREATING_OBJECTIVE)
            objetivo = self._delegar(
                "Objectives",
                "crear_objetivo",
                self._objectives.crear_objetivo,
                descripcion,
                criterio_de_exito,
                **kwargs_objetivo,
            )
            execution.objective_id = objetivo.id
            self._persistir(execution)

            # WORKSPACE.md §2: "el Coordinator crea el Workspace como paso
            # previo" a la planificación del Objetivo raíz.
            workspace_id = self._delegar(
                "Workspace",
                "crear",
                self._workspace.crear,
                execution.objective_id,
                configuracion,
            )
            execution.workspace_id = workspace_id
            self._persistir(execution)
            self._delegar("Workspace", "planificando", self._workspace.planificando, workspace_id)
            self._delegar("Objectives", "planificar", self._objectives.planificar, execution.objective_id)

            # Responsabilidad 2: solicitar el Plan.
            self._transicionar(execution, CoordinatorState.PLANNING)
            self._solicitar_plan(execution)

            # Responsabilidad 3: lanzar el Scheduler.
            self._lanzar_scheduling(execution)
        except DelegationError as error:
            self._fallar(execution, motivo=str(error))
            raise
        return execution

    def _solicitar_plan(self, execution: CoordinatorExecution, motivo_replan: str | None = None) -> None:
        # Fase 2 (Task Generation): Objective -> TaskGenerationPort ->
        # TaskSpec[] -> Planner. El Coordinator no decide ni construye
        # cómo se generan las tareas, solo obtiene el Objetivo y delega su
        # especificación de trabajo en TaskGenerationPort.
        objetivo = self._delegar("Objectives", "obtener", self._objectives.obtener, execution.objective_id)
        tasks = self._delegar("TaskGeneration", "generar", self._task_generation.generar, objetivo)

        if motivo_replan is None:
            plan = self._delegar("Planner", "planificar", self._planner.planificar, execution.objective_id, tasks)
        else:
            plan = self._delegar(
                "Planner",
                "replanificar",
                self._planner.replanificar,
                execution.objective_id,
                motivo_replan,
                tasks,
            )
        execution.plan_id = plan.id
        self._persistir(execution)
        if motivo_replan is None:
            # PLANNER.md §8: la activación del Plan precede inmediatamente
            # a `Objetivo.iniciar()` -- pero eso solo aplica al primer
            # Plan (`planificando -> en_progreso`, OBJECTIVES.md §5). En
            # una replanificación (Fase 5), el Objetivo ya llegó aquí
            # `en_progreso` -- lo dejó así `Objectives.reintentar()`
            # (`fallando -> en_progreso`, único origen legal de esa
            # transición) -- así que volver a invocar `iniciar()` sería
            # `en_progreso -> en_progreso`, no contemplado en la máquina
            # de estados (§5, Invariante: "solo existen las transiciones
            # dibujadas").
            self._delegar("Objectives", "iniciar", self._objectives.iniciar, execution.objective_id)

    def _lanzar_scheduling(self, execution: CoordinatorExecution) -> None:
        self._delegar("Workspace", "scheduling", self._workspace.scheduling, execution.workspace_id)
        self._transicionar(execution, CoordinatorState.SCHEDULING)
        # Decisión de API (ports.py, SchedulerPort.crear): el
        # `scheduler_id` coincide con el `plan_id` -- el Coordinator no
        # persiste un identificador de Scheduler propio (fuera de la
        # lista cerrada de "ESTADO INTERNO" de la ORDEN MAESTRA).
        scheduler_id = self._delegar("Scheduler", "crear", self._scheduler.crear, execution.plan_id)
        self._delegar("Workspace", "ejecutando", self._workspace.ejecutando, execution.workspace_id)
        self._transicionar(execution, CoordinatorState.RUNNING)
        self._delegar("Scheduler", "iniciar", self._scheduler.iniciar, scheduler_id)

    # ---- responsabilidad 4-6: esperar finalización, verificar, replanificar ----

    def _on_scheduler_finalizado(self, **payload: Any) -> None:
        """Reacciona a `scheduler_finalizado` (SCHEDULER.md §10):
        "Garantiza que todas las tareas alcanzaron estados terminales".
        Cubre las responsabilidades 4-6 de la ORDEN MAESTRA."""
        scheduler_id = payload.get("scheduler_id") or payload.get("plan_id")
        execution = self._buscar_por_plan(scheduler_id) if scheduler_id else None
        if execution is None:
            return  # evento de una ejecución ajena o ya terminal -- idempotente
        try:
            self._delegar("Workspace", "verificando", self._workspace.verificando, execution.workspace_id)
            self._transicionar(execution, CoordinatorState.VERIFYING)

            objetivo = self._delegar("Objectives", "obtener", self._objectives.obtener, execution.objective_id)
            evidence = payload.get("evidence", payload)
            resultado = self._delegar(
                "Verifier",
                "verificar",
                self._verifier.verificar,
                evidence,
                objetivo.criterio_de_exito,
                objetivo.confianza_minima,
            )

            if resultado.dictamen == _DICTAMEN_APROBADO:
                self._aprobar(execution, resultado)
            else:
                self._replanificar_o_fallar(execution, resultado)
        except DelegationError as error:
            self._fallar(execution, motivo=str(error))

    def _aprobar(self, execution: CoordinatorExecution, resultado: Any) -> None:
        self._delegar("Objectives", "verificar", self._objectives.verificar, execution.objective_id, resultado)
        self._transicionar(execution, CoordinatorState.AWAITING_WORKSPACE)
        # Responsabilidad 7: esperar Workspace COMPLETED -- el Coordinator
        # dispara la transición (único invocador autorizado, WORKSPACE.md
        # §12) y el resto del flujo continúa en `_on_workspace_completado`,
        # reaccionando al evento `workspace_completado` que esa llamada
        # emite, nunca encadenando la responsabilidad 8 aquí directamente.
        self._delegar("Workspace", "completar", self._workspace.completar, execution.workspace_id)

    def _replanificar_o_fallar(self, execution: CoordinatorExecution, resultado: Any) -> None:
        motivo = getattr(resultado, "justificacion", None) or resultado.dictamen
        # Bugfix Fase 5: el VerificationResult que ya emitió el Verifier
        # (rechazado) debe aplicarse al Objective ANTES de decidir si se
        # reintenta -- OBJECTIVES.md §8 `verificar` es la única operación
        # que puede moverlo de `en_progreso` a `verificando` -> `fallando`,
        # el estado contractual que `reintentar()` exige como origen legal
        # (§5). El Coordinator no vuelve a juzgar la evidencia (eso ya lo
        # decidió el Verifier, única autoridad): solo transporta el
        # dictamen ya decidido, exactamente como en `_aprobar`.
        self._delegar("Objectives", "verificar", self._objectives.verificar, execution.objective_id, resultado)
        try:
            self._delegar("Objectives", "reintentar", self._objectives.reintentar, execution.objective_id)
        except RetriesExhaustedError:
            # OBJECTIVES.md §5: reintentos agotados -> `fallido`. El
            # Coordinator no decide el límite (fuera de su alcance,
            # OBJECTIVES.md §11); solo reacciona a la señal de dominio.
            self._delegar("Workspace", "fallar", self._workspace.fallar, execution.workspace_id, motivo)
            self._fallar(execution, motivo=motivo)
            return

        # Responsabilidad 6: gestionar la replanificación (PLANNER.md §7:
        # siempre una versión nueva de Plan, nunca edición del anterior).
        self._transicionar(execution, CoordinatorState.REPLANNING, motivo=motivo)
        self._delegar("Workspace", "planificando", self._workspace.planificando, execution.workspace_id)
        self._transicionar(execution, CoordinatorState.PLANNING)
        self._solicitar_plan(execution, motivo_replan=motivo)
        self._lanzar_scheduling(execution)

    # ---- responsabilidad 7-9: workspace completado -> package -> fin ----

    def _on_workspace_completado(self, **payload: Any) -> None:
        workspace_id = payload.get("workspace_id")
        execution = self._buscar_por_workspace(workspace_id) if workspace_id else None
        if execution is None:
            return
        try:
            self._transicionar(execution, CoordinatorState.PACKAGING)
            workspace_ref = self._delegar("Workspace", "obtener", self._workspace.obtener, execution.workspace_id)
            pkg = self._delegar("Package", "solicitar", self._package.solicitar, workspace_ref)
            execution.package_id = pkg.id
            self._persistir(execution)
            pkg = self._delegar("Package", "construir", self._package.construir, pkg.id, workspace_ref)
            estado = pkg.estado
            if not isinstance(estado, str):
                estado = getattr(estado, "value", str(estado))
            estado = estado.lower()
            if estado == _ESTADO_PACKAGE_READY:
                # Responsabilidad 9: finalizar la ejecución.
                self._transicionar(execution, CoordinatorState.COMPLETED)
            else:
                self._fallar(execution, motivo="El Package no alcanzó READY.")
        except DelegationError as error:
            self._fallar(execution, motivo=str(error))

    def _on_workspace_fallido(self, **payload: Any) -> None:
        workspace_id = payload.get("workspace_id")
        execution = self._buscar_por_workspace(workspace_id) if workspace_id else None
        if execution is None:
            return
        self._fallar(execution, motivo=payload.get("motivo", "workspace_fallido"))

    # ---- cancelación ----

    def cancelar_ejecucion(self, execution_id: str, motivo: str) -> CoordinatorExecution:
        execution = self._require(execution_id)
        if execution.es_terminal():
            raise CoordinatorImmutableError(execution_id)
        # Cascada best-effort: el Coordinator delega la cancelación en
        # cada módulo que llegó a tener estado propio para esta ejecución;
        # cada módulo decide, dentro de su propio contrato, qué cascada
        # aplicar (OBJECTIVES.md §8, SCHEDULER.md §9.5, WORKSPACE.md §3).
        if execution.workspace_id:
            try:
                self._workspace.cancelar(execution.workspace_id, motivo)
            except Exception:  # noqa: BLE001 -- best-effort, no bloquea la cancelación
                pass
        if execution.plan_id:
            try:
                self._scheduler.cancelar(execution.plan_id, motivo)
            except Exception:  # noqa: BLE001
                pass
        if execution.objective_id:
            try:
                self._objectives.cancelar(execution.objective_id, motivo)
            except Exception:  # noqa: BLE001
                pass
        self._transicionar(execution, CoordinatorState.CANCELLED, motivo=motivo)
        return execution

    # ---- recuperación (ORDEN MAESTRA, "RECUPERACIÓN") ----

    def recuperar(self, execution_id: str) -> CoordinatorExecution:
        """Auditoría/lectura de UNA `CoordinatorExecution` concreta tras un
        reinicio de proceso: (1) verifica que su estructura en disco
        existe, (2) carga el snapshot como referencia de auditoría --
        nunca como fuente de `estado` --, (3) recupera la entidad desde el
        `CoordinatorStore` (la fuente real), (4) si no es terminal, anota
        `ejecucion_recuperada` (una única entrada por invocación) dejando
        su `estado` intacto; si ya es terminal, no muta nada. Mismo
        procedimiento que `PackageManager.abrir` (Fase 12).

        Corrección explícita (antes este docstring citaba textualmente "Tras
        reiniciar EON deberá ser capaz de continuar exactamente donde
        estaba... nunca reiniciará una ejecución desde cero"): este método
        NUNCA reanuda la orquestación -- no reconstruye Objective, Plan,
        Scheduler ni el Worker asignado, y una ejecución no-terminal sigue
        no-terminal después de llamarlo, simplemente auditada. Es una
        lectura con efecto de bitácora, no una recuperación de estado de
        ejecución (CONSOLE.md §9, decisión (b): EON no reanuda de verdad
        en esta fase, solo cierra limpio). Para el caso real de arranque
        del backend de la Console -- cerrar en bloque todo lo que quedó
        huérfano de un proceso anterior -- usar
        `cerrar_ejecuciones_huerfanas` (CONSOLE.md §9.1), no este método.
        """
        layout = self._layout(execution_id)
        if not layout.existe():
            raise CoordinatorRecoveryError(execution_id)
        execution = self._store.get(execution_id)
        if execution is None:
            raise CoordinatorNotFoundError(execution_id)

        cargar_snapshot(layout)  # referencia de auditoría, no fuente de estado

        if not execution.es_terminal():
            execution.registrar_evento(events.EJECUCION_RECUPERADA, de=execution.estado.value, a=execution.estado.value)
            self._persistir(execution)

        self._events.emit(events.EJECUCION_RECUPERADA, execution_id=execution.id)
        return execution

    def cerrar_ejecuciones_huerfanas(
        self, motivo: str = "proceso interrumpido antes de completar"
    ) -> list[CoordinatorExecution]:
        """CONSOLE.md §9.1 (decisión (b): cierre limpio, no resumen real).
        Se llama una única vez, al arrancar un proceso backend que compone
        este `CoordinatorManager` sobre un `root` persistente ya existente
        (p. ej. `eon/console/`) -- nunca durante la ejecución normal ni
        desde `eon.runtime.KernelRuntime`, que sigue sin conocer este
        método.

        Localiza, vía `self.listar()`, las `CoordinatorExecution` que
        quedaron en un estado de `NO_TERMINALES` (models.py) de un proceso
        anterior, y las transiciona directamente a `FAILED` con `motivo`
        (cualquier no-terminal -> FAILED ya es una transición legal,
        `coordinator/validators.py` -- no introduce ningún estado ni
        transición nueva).

        A propósito NO reproduce la cascada best-effort de
        `cancelar_ejecucion` hacia Workspace/Scheduler/Objectives: esa
        cascada tiene sentido sobre un proceso vivo, con esos managers en
        memoria conteniendo de verdad el estado de la ejecución. Aquí, al
        arrancar el backend, `self._workspace`/`self._scheduler`/
        `self._objectives` son `InMemory*` recién creados, siempre vacíos
        respecto a cualquier ejecución de un proceso anterior -- incluir
        esa cascada no sería defensivo, sería aparentar un efecto que
        nunca puede darse en este punto de llamada.

        Idempotente: una segunda llamada no encuentra ya ninguna
        `NO_TERMINAL` que cerrar (`FAILED` ya es terminal). Sin protección
        ante dos procesos backend arrancando a la vez sobre el mismo
        `root` -- fuera de alcance, igual que CONSOLE.md §7 escopa la
        Console a un solo usuario/proceso local por ahora.
        """
        cerradas: list[CoordinatorExecution] = []
        for execution in self.listar():
            if execution.estado not in NO_TERMINALES:
                continue
            self._transicionar(execution, CoordinatorState.FAILED, motivo=motivo)
            cerradas.append(execution)
        return cerradas

    # ---- lectura ----

    def obtener(self, execution_id: str) -> CoordinatorExecution:
        return self._require(execution_id)

    def listar(self) -> list[CoordinatorExecution]:
        return self._store.list()

    def snapshot(self, execution_id: str) -> CoordinatorSnapshot:
        return CoordinatorSnapshot.desde_execution(self._require(execution_id))
