"""
Tests del Scheduler (Fase 9). No dependen de red, LLM ni infraestructura:
usan un EventBus propio auto-contenido (mismo patrón que test_planner.py) y
un InMemorySchedulerStore. No importan `eon.planner`: los Plans se simulan
con `FakePlan`/`FakeTask`, duck-typed (`.id`, `.tasks`, `.depende_de`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from eon.scheduler import (
    DependencyCycleError,
    IllegalSchedulerTransitionError,
    InMemorySchedulerStore,
    InvalidPlanError,
    SchedulerAlreadyExistsError,
    SchedulerManager,
    SchedulerNotFoundError,
    SchedulerState,
    TaskExecutionState,
    TaskNotFoundError,
    events,
)

# ---------------------------------------------------------------------------
# Infraestructura de test (auto-contenida)
# ---------------------------------------------------------------------------


class EventBus:
    """EventBus mínimo, auto-contenido. Misma interfaz (`on`/`emit`) que usa
    SchedulerManager. No forma parte del dominio del Scheduler."""

    def __init__(self):
        self._subs: dict[str, list] = {}

    def on(self, nombre, callback):
        self._subs.setdefault(nombre, []).append(callback)

    def emit(self, nombre, **data):
        for callback in self._subs.get(nombre, []):
            callback(**data)


class _Grabador:
    """Escucha todos los eventos emitidos y los guarda para que los tests
    puedan afirmar exactamente qué se publicó, en qué orden."""

    def __init__(self, bus: EventBus):
        self.recibidos: list[tuple[str, dict]] = []
        for nombre in events.TODOS:
            bus.on(nombre, self._make(nombre))

    def _make(self, nombre):
        def _cb(**data):
            self.recibidos.append((nombre, data))

        return _cb

    def nombres(self) -> list[str]:
        return [n for n, _ in self.recibidos]

    def de_tipo(self, nombre: str) -> list[dict]:
        return [d for n, d in self.recibidos if n == nombre]


@dataclass
class FakeTask:
    id: str
    depende_de: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class FakePlan:
    id: str
    tasks: list[FakeTask]


def plan(plan_id: str, *tasks: FakeTask) -> FakePlan:
    return FakePlan(id=plan_id, tasks=list(tasks))


def t(task_id: str, *deps: str) -> FakeTask:
    return FakeTask(id=task_id, depende_de=tuple(deps))


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def grabador(bus):
    return _Grabador(bus)


@pytest.fixture
def store():
    return InMemorySchedulerStore()


@pytest.fixture
def manager(store, bus):
    return SchedulerManager(store, bus)


# ---------------------------------------------------------------------------
# Creación
# ---------------------------------------------------------------------------


class TestCreacion:
    def test_crea_scheduler_en_idle(self, manager):
        run = manager.crear(plan("p1", t("a")))
        assert run.estado is SchedulerState.IDLE

    def test_emite_scheduler_creado(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        assert events.SCHEDULER_CREADO in grabador.nombres()

    def test_scheduler_creado_incluye_total_tasks(self, manager, grabador):
        manager.crear(plan("p1", t("a"), t("b")))
        [datos] = grabador.de_tipo(events.SCHEDULER_CREADO)
        assert datos["total_tasks"] == 2

    def test_task_sin_dependencias_queda_ready_de_inmediato(self, manager):
        run = manager.crear(plan("p1", t("a")))
        assert run.tasks["a"].estado is TaskExecutionState.READY

    def test_task_con_dependencia_queda_pending(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a")))
        assert run.tasks["b"].estado is TaskExecutionState.PENDING

    def test_ready_inicial_emite_task_ready(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        assert events.TASK_READY in grabador.nombres()

    def test_ready_inicial_no_emite_para_tasks_con_dependencias(self, manager, grabador):
        manager.crear(plan("p1", t("a"), t("b", "a")))
        ids_ready = [d["task_id"] for d in grabador.de_tipo(events.TASK_READY)]
        assert ids_ready == ["a"]

    def test_plan_sin_tasks_lanza_invalid_plan_error(self, manager):
        with pytest.raises(InvalidPlanError):
            manager.crear(plan("p1"))

    def test_dependencia_inexistente_lanza_invalid_plan_error(self, manager):
        with pytest.raises(InvalidPlanError):
            manager.crear(plan("p1", t("a", "fantasma")))

    def test_dependencia_a_si_misma_lanza_invalid_plan_error(self, manager):
        with pytest.raises(InvalidPlanError):
            manager.crear(plan("p1", t("a", "a")))

    def test_ciclo_lanza_dependency_cycle_error(self, manager):
        with pytest.raises(DependencyCycleError):
            manager.crear(plan("p1", t("a", "b"), t("b", "a")))

    def test_task_id_duplicado_dentro_del_mismo_plan_lanza_invalid_plan_error(self, manager):
        """C1: dos Tasks con el mismo id en el mismo Plan se perdían en
        silencio antes de esta corrección -- el dict de tasks se quedaba con
        una sola (la última), pero `orden` conservaba el id duplicado dos
        veces, corrompiendo colas y el recálculo de READY."""
        with pytest.raises(InvalidPlanError):
            manager.crear(plan("p1", t("a"), t("a")))

    def test_task_id_duplicado_no_dejo_el_plan_a_medio_crear(self, manager, store):
        """Tras el rechazo, no debe quedar un SchedulerRun corrupto o parcial
        para 'p1' -- ni en el store ni indexado por task_id."""
        with pytest.raises(InvalidPlanError):
            manager.crear(plan("p1", t("a"), t("a")))
        with pytest.raises(SchedulerNotFoundError):
            manager.snapshot("p1")

    def test_plan_duplicado_lanza_scheduler_already_exists(self, manager):
        manager.crear(plan("p1", t("a")))
        with pytest.raises(SchedulerAlreadyExistsError):
            manager.crear(plan("p1", t("a")))

    def test_snapshot_tras_crear_refleja_colas(self, manager):
        manager.crear(plan("p1", t("a"), t("b", "a")))
        snap = manager.snapshot("p1")
        assert snap.cola_ready == ("a",)
        assert snap.cola_pending == ("b",)

    def test_orden_determinista_en_cola_pending(self, manager):
        manager.crear(plan("p1", t("z", "a"), t("y", "a"), t("a")))
        snap = manager.snapshot("p1")
        assert snap.cola_pending == ("z", "y")

    def test_multiples_tasks_sin_dependencias_quedan_todas_ready(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b"), t("c")))
        assert run.tasks["a"].estado is TaskExecutionState.READY
        assert run.tasks["b"].estado is TaskExecutionState.READY
        assert run.tasks["c"].estado is TaskExecutionState.READY


# ---------------------------------------------------------------------------
# Inicio
# ---------------------------------------------------------------------------


class TestInicio:
    def test_iniciar_pasa_a_running(self, manager):
        manager.crear(plan("p1", t("a")))
        run = manager.iniciar("p1")
        assert run.estado is SchedulerState.RUNNING

    def test_iniciar_emite_scheduler_iniciado(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        assert events.SCHEDULER_INICIADO in grabador.nombres()

    def test_iniciar_plan_inexistente_lanza_not_found(self, manager):
        with pytest.raises(SchedulerNotFoundError):
            manager.iniciar("fantasma")

    def test_doble_iniciar_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.iniciar("p1")

    def test_iniciar_tras_stopped_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.cancelar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.iniciar("p1")


# ---------------------------------------------------------------------------
# Pausa
# ---------------------------------------------------------------------------


class TestPausa:
    def test_pausar_desde_running(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        run = manager.pausar("p1")
        assert run.estado is SchedulerState.PAUSED

    def test_pausar_emite_scheduler_pausado(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.pausar("p1")
        assert events.SCHEDULER_PAUSADO in grabador.nombres()

    def test_pausar_desde_idle_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.pausar("p1")

    def test_doble_pausa_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.pausar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.pausar("p1")

    def test_pausar_plan_inexistente(self, manager):
        with pytest.raises(SchedulerNotFoundError):
            manager.pausar("fantasma")


# ---------------------------------------------------------------------------
# Reanudación
# ---------------------------------------------------------------------------


class TestReanudacion:
    def test_reanudar_desde_paused(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.pausar("p1")
        run = manager.reanudar("p1")
        assert run.estado is SchedulerState.RUNNING

    def test_reanudar_emite_scheduler_reanudado(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.pausar("p1")
        manager.reanudar("p1")
        assert events.SCHEDULER_REANUDADO in grabador.nombres()

    def test_reanudar_desde_idle_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.reanudar("p1")

    def test_reanudar_desde_running_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.reanudar("p1")

    def test_reanudar_desde_stopped_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.cancelar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.reanudar("p1")


# ---------------------------------------------------------------------------
# Cancelación
# ---------------------------------------------------------------------------


class TestCancelacion:
    def test_cancelar_desde_idle(self, manager):
        manager.crear(plan("p1", t("a")))
        run = manager.cancelar("p1")
        assert run.estado is SchedulerState.STOPPED

    def test_cancelar_desde_running(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        run = manager.cancelar("p1")
        assert run.estado is SchedulerState.STOPPED

    def test_cancelar_desde_paused(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.pausar("p1")
        run = manager.cancelar("p1")
        assert run.estado is SchedulerState.STOPPED

    def test_cancelar_emite_scheduler_cancelado(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        manager.cancelar("p1")
        assert events.SCHEDULER_CANCELADO in grabador.nombres()

    def test_cancelar_desde_stopped_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.cancelar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.cancelar("p1")

    def test_cancelar_cascada_cancela_pending_y_ready(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a")))
        manager.cancelar("p1")
        assert run.tasks["a"].estado is TaskExecutionState.CANCELLED
        assert run.tasks["b"].estado is TaskExecutionState.CANCELLED

    def test_cancelar_cancela_tasks_running(self, manager):
        """SCHEDULER.md v1.0: RUNNING también se cancela -- corrección de
        contrato. Antes se dejaba intacta y quedaba huérfana, porque tras
        STOPPED ningún marcar_* la puede resolver ya (ver test siguiente)."""
        run = manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.cancelar("p1")
        assert run.tasks["a"].estado is TaskExecutionState.CANCELLED

    def test_cancelar_running_no_deja_huerfanas(self, manager):
        """Antes de la corrección de contrato, una Task RUNNING en el
        momento de cancelar() se quedaba RUNNING para siempre: ninguna
        operación marcar_* la puede tocar una vez el Scheduler está
        STOPPED. Este test documenta por qué RUNNING -> CANCELLED es
        obligatorio, no solo una opción de diseño."""
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.cancelar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_completed("a")

    def test_cancelar_incluye_running_en_tasks_cancelados(self, manager, grabador):
        manager.crear(plan("p1", t("a"), t("b")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.cancelar("p1")
        [datos] = grabador.de_tipo(events.SCHEDULER_CANCELADO)
        assert set(datos["tasks_cancelados"]) == {"a", "b"}

    def test_cancelar_no_toca_tasks_completed(self, manager):
        run = manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        manager.cancelar("p1")
        assert run.tasks["a"].estado is TaskExecutionState.COMPLETED

    def test_cancelar_incluye_tasks_cancelados_en_evento(self, manager, grabador):
        manager.crear(plan("p1", t("a"), t("b")))
        manager.cancelar("p1")
        [datos] = grabador.de_tipo(events.SCHEDULER_CANCELADO)
        assert set(datos["tasks_cancelados"]) == {"a", "b"}

    def test_cancelar_plan_inexistente(self, manager):
        with pytest.raises(SchedulerNotFoundError):
            manager.cancelar("fantasma")


# ---------------------------------------------------------------------------
# READY por dependencias
# ---------------------------------------------------------------------------


class TestReadyPorDependencias:
    def test_task_pasa_a_ready_tras_completar_su_unica_dependencia(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        assert run.tasks["b"].estado is TaskExecutionState.READY

    def test_completar_dependencia_emite_task_ready_para_la_dependiente(self, manager, grabador):
        manager.crear(plan("p1", t("a"), t("b", "a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        ids_ready = [d["task_id"] for d in grabador.de_tipo(events.TASK_READY)]
        assert ids_ready == ["a", "b"]

    def test_task_con_dos_dependencias_espera_a_ambas(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b"), t("c", "a", "b")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        assert run.tasks["c"].estado is TaskExecutionState.PENDING

    def test_task_con_dos_dependencias_queda_ready_al_completar_la_segunda(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b"), t("c", "a", "b")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        manager.marcar_running("b")
        manager.marcar_completed("b")
        assert run.tasks["c"].estado is TaskExecutionState.READY

    def test_cadena_larga_de_dependencias(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a"), t("c", "b"), t("d", "c")))
        manager.iniciar("p1")
        for tid in ("a", "b", "c"):
            manager.marcar_running(tid)
            manager.marcar_completed(tid)
        assert run.tasks["d"].estado is TaskExecutionState.READY

    def test_diamante_de_dependencias(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a"), t("c", "a"), t("d", "b", "c")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        manager.marcar_running("b")
        manager.marcar_completed("b")
        assert run.tasks["d"].estado is TaskExecutionState.PENDING
        manager.marcar_running("c")
        manager.marcar_completed("c")
        assert run.tasks["d"].estado is TaskExecutionState.READY

    def test_ready_tasks_devuelve_ids_en_estado_ready(self, manager):
        manager.crear(plan("p1", t("a"), t("b", "a")))
        assert manager.ready_tasks("p1") == ["a"]


# ---------------------------------------------------------------------------
# Bloqueo por FAILED
# ---------------------------------------------------------------------------


class TestBloqueoPorFailed:
    def test_dependencia_directa_queda_blocked_si_falla_su_dependencia(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        assert run.tasks["b"].estado is TaskExecutionState.BLOCKED

    def test_falla_emite_task_failed(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        assert events.TASK_FAILED in grabador.nombres()

    def test_bloqueo_emite_task_blocked(self, manager, grabador):
        manager.crear(plan("p1", t("a"), t("b", "a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        ids_bloqueadas = [d["task_id"] for d in grabador.de_tipo(events.TASK_BLOCKED)]
        assert ids_bloqueadas == ["b"]

    def test_tasks_independientes_no_se_bloquean(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a"), t("c")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        assert run.tasks["c"].estado is TaskExecutionState.READY

    def test_marcar_failed_task_no_running_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_failed("a")

    def test_marcar_failed_con_scheduler_pausado_es_ilegal(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.pausar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_failed("a")


# ---------------------------------------------------------------------------
# Propagación de bloqueos
# ---------------------------------------------------------------------------


class TestPropagacionDeBloqueos:
    def test_bloqueo_transitivo_en_cadena(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a"), t("c", "b")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        assert run.tasks["b"].estado is TaskExecutionState.BLOCKED
        assert run.tasks["c"].estado is TaskExecutionState.BLOCKED

    def test_bloqueo_transitivo_emite_evento_por_cada_bloqueada(self, manager, grabador):
        manager.crear(plan("p1", t("a"), t("b", "a"), t("c", "b")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        ids = [d["task_id"] for d in grabador.de_tipo(events.TASK_BLOCKED)]
        assert ids == ["b", "c"]

    def test_diamante_con_una_rama_fallida_bloquea_solo_esa_rama(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a"), t("c", "a"), t("d", "b", "c")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        manager.marcar_running("b")
        manager.marcar_failed("b")
        assert run.tasks["c"].estado is TaskExecutionState.READY
        assert run.tasks["d"].estado is TaskExecutionState.BLOCKED

    def test_multiples_fallos_independientes_bloquean_cada_rama(self, manager):
        run = manager.crear(plan("p1", t("a"), t("x"), t("b", "a"), t("y", "x")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        manager.marcar_running("x")
        manager.marcar_failed("x")
        assert run.tasks["b"].estado is TaskExecutionState.BLOCKED
        assert run.tasks["y"].estado is TaskExecutionState.BLOCKED


# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------


class TestEventos:
    def test_orden_de_eventos_para_flujo_simple(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        assert grabador.nombres() == [
            events.SCHEDULER_CREADO,
            events.TASK_READY,
            events.SCHEDULER_INICIADO,
            events.TASK_RUNNING,
            events.TASK_COMPLETED,
        ]

    def test_marcar_running_emite_exactamente_un_evento(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        antes = len(grabador.recibidos)
        manager.marcar_running("a")
        assert len(grabador.recibidos) - antes == 1

    def test_eventos_incluyen_plan_id(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        [datos] = grabador.de_tipo(events.SCHEDULER_CREADO)
        assert datos["plan_id"] == "p1"

    def test_task_events_incluyen_task_id(self, manager, grabador):
        manager.crear(plan("p1", t("a")))
        [datos] = grabador.de_tipo(events.TASK_READY)
        assert datos["task_id"] == "a"

    def test_no_existe_evento_task_cancelled_en_catalogo(self):
        assert "task_cancelled" not in events.TODOS

    def test_catalogo_de_eventos_tiene_los_diez_definidos(self):
        assert len(events.TODOS) == 10


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_incluye_plan_id_y_estado(self, manager):
        manager.crear(plan("p1", t("a")))
        snap = manager.snapshot("p1")
        assert snap.plan_id == "p1"
        assert snap.estado is SchedulerState.IDLE

    def test_snapshot_tras_marcar_running(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        snap = manager.snapshot("p1")
        assert snap.cola_running == ("a",)
        assert snap.cola_ready == ()

    def test_snapshot_tras_completar_todas(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        snap = manager.snapshot("p1")
        assert snap.cola_completed == ("a",)

    def test_snapshot_tras_fallo(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        snap = manager.snapshot("p1")
        assert snap.cola_failed == ("a",)

    def test_snapshot_tras_cancelar_con_task_running_no_deja_cola_running(self, manager):
        """Tras cancelar(), ninguna Task debe seguir apareciendo en
        cola_running -- RUNNING -> CANCELLED (SCHEDULER.md v1.0), así que
        snapshot() nunca debería mostrar 'trabajo en curso' en un Scheduler
        ya STOPPED."""
        manager.crear(plan("p1", t("a"), t("b")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.cancelar("p1")
        snap = manager.snapshot("p1")
        assert snap.cola_running == ()
        assert snap.estado is SchedulerState.STOPPED

    def test_snapshot_estado_stopped_tras_cancelar(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.cancelar("p1")
        snap = manager.snapshot("p1")
        assert snap.estado is SchedulerState.STOPPED

    def test_actualizado_en_cambia_tras_una_transicion(self, manager):
        manager.crear(plan("p1", t("a")))
        snap_inicial = manager.snapshot("p1")
        manager.iniciar("p1")
        snap_final = manager.snapshot("p1")
        assert snap_final.actualizado_en >= snap_inicial.actualizado_en

    def test_snapshot_plan_inexistente(self, manager):
        with pytest.raises(SchedulerNotFoundError):
            manager.snapshot("fantasma")


# ---------------------------------------------------------------------------
# Invariantes
# ---------------------------------------------------------------------------


class TestInvariantes:
    def test_task_nunca_se_ejecuta_dos_veces(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_running("a")

    def test_completed_nunca_vuelve(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_completed("a")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_completed("a")

    def test_failed_nunca_vuelve_a_ready(self, manager):
        run = manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.marcar_failed("a")
        assert run.tasks["a"].estado is TaskExecutionState.FAILED
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_running("a")

    def test_cancelled_nunca_vuelve(self, manager):
        run = manager.crear(plan("p1", t("a")))
        manager.cancelar("p1")
        assert run.tasks["a"].estado is TaskExecutionState.CANCELLED
        manager.crear(plan("p2", t("z")))
        manager.iniciar("p2")
        # Un plan CANCELLED no puede reanudarse (el propio Scheduler ya
        # está STOPPED, terminal); se verifica indirectamente vía cancelar.
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.cancelar("p1")

    def test_ready_implica_dependencias_satisfechas(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a")))
        assert run.tasks["b"].estado is not TaskExecutionState.READY

    def test_running_implica_ready_previo(self, manager):
        manager.crear(plan("p1", t("a"), t("b", "a")))
        manager.iniciar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_running("b")

    def test_task_pertenece_a_un_unico_plan(self, manager):
        manager.crear(plan("p1", t("a")))
        with pytest.raises(InvalidPlanError):
            manager.crear(plan("p2", t("a")))  # mismo id de task, plan distinto

    def test_task_id_desconocido_lanza_task_not_found(self, manager):
        with pytest.raises(TaskNotFoundError):
            manager.marcar_running("fantasma")


# ---------------------------------------------------------------------------
# Transiciones ilegales
# ---------------------------------------------------------------------------


class TestExcepciones:
    """C2: IllegalSchedulerTransitionError distingue explícitamente
    'operación no aplicable' (verbo) de 'transición ilegal' (estado ->
    estado). Antes ambas reutilizaban `destino` para cosas distintas y el
    mensaje de una operación fallida mostraba algo como \"'idle' -> 'pausar'\"
    -- una supuesta transición hacia un verbo."""

    def test_operacion_invalida_no_menciona_flecha_de_estados(self, manager):
        manager.crear(plan("p1", t("a")))
        with pytest.raises(IllegalSchedulerTransitionError) as exc_info:
            manager.pausar("p1")  # pausar() no es válido desde IDLE
        mensaje = str(exc_info.value)
        assert "->" not in mensaje
        assert "'pausar'" in mensaje
        assert exc_info.value.operacion == "pausar"
        assert exc_info.value.destino is None

    def test_transicion_de_task_si_menciona_flecha_de_estados(self, manager):
        manager.crear(plan("p1", t("a"), t("b", "a")))
        manager.iniciar("p1")
        with pytest.raises(IllegalSchedulerTransitionError) as exc_info:
            manager.marcar_running("b")  # "b" sigue PENDING, no READY
        mensaje = str(exc_info.value)
        assert "->" in mensaje
        assert exc_info.value.destino == "running"
        assert exc_info.value.operacion is None

    def test_constructor_exige_destino_xor_operacion(self):
        with pytest.raises(ValueError):
            IllegalSchedulerTransitionError("idle")
        with pytest.raises(ValueError):
            IllegalSchedulerTransitionError("idle", "running", operacion="iniciar")


class TestTransicionesIlegales:
    def test_marcar_running_sin_estar_ready(self, manager):
        manager.crear(plan("p1", t("a"), t("b", "a")))
        manager.iniciar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_running("b")

    def test_marcar_completed_sin_estar_running(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_completed("a")

    def test_marcar_running_con_scheduler_idle(self, manager):
        manager.crear(plan("p1", t("a")))
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_running("a")

    def test_marcar_running_con_scheduler_pausado(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.pausar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_running("a")

    def test_marcar_completed_con_scheduler_stopped(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.iniciar("p1")
        manager.marcar_running("a")
        manager.cancelar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.marcar_completed("a")

    def test_pausar_scheduler_ya_stopped(self, manager):
        manager.crear(plan("p1", t("a")))
        manager.cancelar("p1")
        with pytest.raises(IllegalSchedulerTransitionError):
            manager.pausar("p1")


# ---------------------------------------------------------------------------
# Ciclos
# ---------------------------------------------------------------------------


class TestCiclos:
    def test_ciclo_simple(self, manager):
        with pytest.raises(DependencyCycleError):
            manager.crear(plan("p1", t("a", "b"), t("b", "a")))

    def test_ciclo_largo(self, manager):
        with pytest.raises(DependencyCycleError):
            manager.crear(plan("p1", t("a", "c"), t("b", "a"), t("c", "b")))

    def test_ciclo_con_rama_valida_tambien_falla(self, manager):
        with pytest.raises(DependencyCycleError):
            manager.crear(plan("p1", t("a"), t("b", "c"), t("c", "b")))

    def test_grafo_sin_ciclo_no_lanza(self, manager):
        run = manager.crear(plan("p1", t("a"), t("b", "a"), t("c", "b")))
        assert run.plan_id == "p1"


# ---------------------------------------------------------------------------
# Múltiples planes independientes
# ---------------------------------------------------------------------------


class TestMultiplesPlanes:
    def test_dos_planes_no_interfieren(self, manager):
        run1 = manager.crear(plan("p1", t("a")))
        run2 = manager.crear(plan("p2", t("b")))
        manager.iniciar("p1")
        assert run1.estado is SchedulerState.RUNNING
        assert run2.estado is SchedulerState.IDLE

    def test_cancelar_un_plan_no_afecta_al_otro(self, manager):
        manager.crear(plan("p1", t("a")))
        run2 = manager.crear(plan("p2", t("b")))
        manager.cancelar("p1")
        assert run2.estado is SchedulerState.IDLE

    def test_marcar_completed_en_un_plan_no_afecta_al_otro(self, manager):
        run1 = manager.crear(plan("p1", t("x")))
        run2 = manager.crear(plan("p2", t("w")))
        manager.iniciar("p1")
        manager.iniciar("p2")
        manager.marcar_running("x")
        assert run1.tasks["x"].estado is TaskExecutionState.RUNNING
        assert run2.tasks["w"].estado is TaskExecutionState.READY

    def test_store_lista_todos_los_planes(self, manager, store):
        manager.crear(plan("p1", t("a")))
        manager.crear(plan("p2", t("b")))
        assert {r.plan_id for r in store.listar()} == {"p1", "p2"}


# ---------------------------------------------------------------------------
# Orden determinista
# ---------------------------------------------------------------------------


class TestOrdenDeterminista:
    def test_cola_ready_respeta_orden_de_insercion(self, manager):
        manager.crear(plan("p1", t("c"), t("b"), t("a")))
        snap = manager.snapshot("p1")
        assert snap.cola_ready == ("c", "b", "a")

    def test_ready_tasks_es_estable_entre_llamadas(self, manager):
        manager.crear(plan("p1", t("c"), t("b"), t("a")))
        primera = manager.ready_tasks("p1")
        segunda = manager.ready_tasks("p1")
        assert primera == segunda == ["c", "b", "a"]

    def test_recalculo_respeta_orden_original_no_orden_de_finalizacion(self, manager):
        run = manager.crear(plan("p1", t("z"), t("y"), t("dep_a", "z"), t("dep_b", "y")))
        manager.iniciar("p1")
        # Se completa "y" antes que "z", pero el orden del Plan es z, y.
        manager.marcar_running("y")
        manager.marcar_completed("y")
        manager.marcar_running("z")
        manager.marcar_completed("z")
        snap = manager.snapshot("p1")
        assert snap.cola_ready == ("dep_a", "dep_b")
