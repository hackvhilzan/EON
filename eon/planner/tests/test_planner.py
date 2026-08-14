"""
Tests del Planner Engine (Fase 8). No dependen de red ni de LLM real: usan un
EventBus propio (auto-contenido -- este paquete no importa eon.core, ver
eon/planner/__init__.py) y un InMemoryPlannerStore.
"""

from __future__ import annotations

import pytest
from eon.planner import (
    IllegalPlanTransitionError,
    InMemoryPlannerStore,
    InvalidPlanError,
    InvalidTaskGraphError,
    Plan,
    PlannerManager,
    PlanNotFoundError,
    PlanState,
    Task,
    TaskGraph,
    construir,
    construir_candidatos,
    events,
    menor_numero_de_tasks,
    primero,
    seleccionar,
)


class EventBus:
    """EventBus mínimo, auto-contenido para no depender de eon.core (que no
    forma parte del dominio de Plan -- PLANNER.md §0: "no conoce
    infraestructura"). Misma interfaz (`on`/`emit`) que usa PlannerManager."""

    def __init__(self):
        self._subs: dict[str, list] = {}

    def on(self, nombre, callback):
        self._subs.setdefault(nombre, []).append(callback)

    def emit(self, nombre, **data):
        for callback in self._subs.get(nombre, []):
            callback(**data)


class _Grabador:
    """Escucha todos los eventos emitidos y los guarda para que los tests puedan
    afirmar exactamente qué se publicó, en qué orden."""

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


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def grabador(bus):
    return _Grabador(bus)


@pytest.fixture
def store():
    return InMemoryPlannerStore()


@pytest.fixture
def manager(store, bus):
    return PlannerManager(store, bus)


def tasks(*capability_ids, **kwargs):
    """Azúcar para tests: una Task por capability_id, sin dependencias, salvo
    que se pasen specs completas en `especificaciones`."""
    especificaciones = kwargs.get("especificaciones")
    if especificaciones is not None:
        return construir(especificaciones)
    return construir([{"capability_id": cid} for cid in capability_ids])


# ---------------------------------------------------------------------------
# Task / TaskGraph
# ---------------------------------------------------------------------------


class TestTask:
    def test_task_necesita_capability_id(self):
        with pytest.raises(InvalidPlanError):
            Task(capability_id="")

    def test_task_no_puede_depender_de_si_misma(self):
        with pytest.raises(InvalidPlanError):
            Task(capability_id="cap.a", id="t1", depende_de=("t1",))

    def test_task_es_inmutable(self):
        t = Task(capability_id="cap.a")
        with pytest.raises(Exception):
            t.capability_id = "cap.b"  # frozen dataclass -> FrozenInstanceError

    def test_to_dict(self):
        t = Task(capability_id="cap.a", parametros={"x": 1})
        d = t.to_dict()
        assert d["capability_id"] == "cap.a"
        assert d["parametros"] == {"x": 1}
        assert isinstance(d["depende_de"], list)


class TestTaskGraph:
    def test_orden_total_respeta_dependencias(self):
        a = Task(capability_id="cap.a", id="a")
        b = Task(capability_id="cap.b", id="b", depende_de=("a",))
        c = Task(capability_id="cap.c", id="c", depende_de=("b",))
        orden = TaskGraph([c, a, b]).orden_total()
        assert [t.id for t in orden] == ["a", "b", "c"]

    def test_detecta_ciclo(self):
        a = Task(capability_id="cap.a", id="a", depende_de=("b",))
        b = Task(capability_id="cap.b", id="b", depende_de=("a",))
        with pytest.raises(InvalidTaskGraphError):
            TaskGraph([a, b]).validar()

    def test_detecta_dependencia_fuera_del_plan(self):
        a = Task(capability_id="cap.a", id="a", depende_de=("no-existe",))
        with pytest.raises(InvalidTaskGraphError):
            TaskGraph([a]).validar()

    def test_orden_total_es_determinista_sin_dependencias(self):
        a = Task(capability_id="cap.a", id="a")
        b = Task(capability_id="cap.b", id="b")
        assert [t.id for t in TaskGraph([a, b]).orden_total()] == ["a", "b"]
        assert [t.id for t in TaskGraph([b, a]).orden_total()] == ["b", "a"]


class TestStrategyBuilder:
    def test_construir_rechaza_lista_vacia(self):
        with pytest.raises(InvalidPlanError):
            construir([])

    def test_construir_rechaza_spec_sin_capability_id(self):
        with pytest.raises(InvalidPlanError):
            construir([{"parametros": {}}])

    def test_construir_devuelve_orden_total(self):
        resultado = construir(
            [
                {"capability_id": "cap.b", "id": "b", "depende_de": ["a"]},
                {"capability_id": "cap.a", "id": "a"},
            ]
        )
        assert [t.id for t in resultado] == ["a", "b"]

    def test_construir_candidatos_valida_cada_uno_por_separado(self):
        candidatos = construir_candidatos(
            [
                [{"capability_id": "cap.x"}],
                [{"capability_id": "cap.y"}],
            ]
        )
        assert len(candidatos) == 2
        assert candidatos[0][0].capability_id == "cap.x"

    def test_construir_candidatos_rechaza_lista_vacia(self):
        with pytest.raises(InvalidPlanError):
            construir_candidatos([])


# ---------------------------------------------------------------------------
# Plan (modelo)
# ---------------------------------------------------------------------------


class TestPlanModel:
    def test_plan_necesita_objective_id(self):
        with pytest.raises(InvalidPlanError):
            Plan(objective_id="", tasks=tasks("cap.a"))

    def test_plan_necesita_al_menos_una_task(self):
        with pytest.raises(InvalidPlanError):
            Plan(objective_id="obj-1", tasks=[])

    def test_plan_nace_en_creado(self):
        plan = Plan(objective_id="obj-1", tasks=tasks("cap.a"))
        assert plan.estado == PlanState.CREADO

    def test_plan_version_minima_1(self):
        with pytest.raises(InvalidPlanError):
            Plan(objective_id="obj-1", tasks=tasks("cap.a"), version=0)

    def test_to_dict_serializa_estado_y_tasks(self):
        plan = Plan(objective_id="obj-1", tasks=tasks("cap.a", "cap.b"))
        d = plan.to_dict()
        assert d["estado"] == "creado"
        assert len(d["tasks"]) == 2


# ---------------------------------------------------------------------------
# PlannerManager -- nacimiento (§2)
# ---------------------------------------------------------------------------


class TestNacimiento:
    def test_crear_plan_nace_en_creado_version_1(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        assert plan.estado == PlanState.CREADO
        assert plan.version == 1

    def test_crear_plan_emite_plan_creado(self, manager, grabador):
        manager.crear_plan("obj-1", tasks("cap.a"))
        assert grabador.nombres() == [events.PLAN_CREADO]

    def test_replanificar_incrementa_version_y_emite_plan_replanificado(self, manager, grabador):
        manager.crear_plan("obj-1", tasks("cap.a"))
        plan2 = manager.replanificar("obj-1", tasks("cap.b"))
        assert plan2.version == 2
        assert grabador.nombres() == [events.PLAN_CREADO, events.PLAN_REPLANIFICADO]

    def test_reintentar_incrementa_version_y_emite_plan_creado(self, manager, grabador):
        manager.crear_plan("obj-1", tasks("cap.a"))
        plan2 = manager.reintentar("obj-1", tasks("cap.b"))
        assert plan2.version == 2
        assert grabador.nombres() == [events.PLAN_CREADO, events.PLAN_CREADO]

    def test_version_se_incrementa_sin_importar_el_origen(self, manager):
        """PLANNER.md §1 (regla de cierre): version cuenta todo Plan nuevo,
        sin importar si es planificación inicial, replanificación o reintento."""
        p1 = manager.crear_plan("obj-1", tasks("cap.a"))
        p2 = manager.replanificar("obj-1", tasks("cap.b"))
        p3 = manager.reintentar("obj-1", tasks("cap.c"))
        assert [p1.version, p2.version, p3.version] == [1, 2, 3]

    def test_planes_de_objetivos_distintos_versionan_independientemente(self, manager):
        p1 = manager.crear_plan("obj-1", tasks("cap.a"))
        p2 = manager.crear_plan("obj-2", tasks("cap.b"))
        assert p1.version == 1
        assert p2.version == 1

    def test_nunca_nace_activo(self, manager):
        for plan in (
            manager.crear_plan("obj-1", tasks("cap.a")),
            manager.replanificar("obj-1", tasks("cap.b")),
            manager.reintentar("obj-1", tasks("cap.c")),
        ):
            assert plan.estado == PlanState.CREADO


# ---------------------------------------------------------------------------
# PlannerManager -- activación (§5, §8)
# ---------------------------------------------------------------------------


class TestActivacion:
    def test_activar_transiciona_a_activo(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(plan.id)
        assert manager.obtener(plan.id).estado == PlanState.ACTIVO

    def test_activar_emite_plan_activado(self, manager, grabador):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(plan.id)
        assert grabador.nombres() == [events.PLAN_CREADO, events.PLAN_ACTIVADO]

    def test_activo_de_devuelve_el_plan_activo(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        assert manager.activo_de("obj-1") is None
        manager.activar(plan.id)
        assert manager.activo_de("obj-1").id == plan.id

    def test_activar_nuevo_plan_obsoleta_el_anterior(self, manager):
        p1 = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(p1.id)
        p2 = manager.replanificar("obj-1", tasks("cap.b"))
        manager.activar(p2.id)
        assert manager.obtener(p1.id).estado == PlanState.OBSOLETO
        assert manager.obtener(p2.id).estado == PlanState.ACTIVO
        assert manager.activo_de("obj-1").id == p2.id

    def test_nunca_dos_planes_activos_para_el_mismo_objetivo(self, manager):
        p1 = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(p1.id)
        p2 = manager.replanificar("obj-1", tasks("cap.b"))
        manager.activar(p2.id)
        activos = [p for p in manager.historial_de("obj-1") if p.estado == PlanState.ACTIVO]
        assert len(activos) == 1

    def test_activar_candidato_no_afecta_planes_de_otro_objetivo(self, manager):
        p_obj1 = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(p_obj1.id)
        p_obj2 = manager.crear_plan("obj-2", tasks("cap.b"))
        manager.activar(p_obj2.id)
        assert manager.obtener(p_obj1.id).estado == PlanState.ACTIVO
        assert manager.obtener(p_obj2.id).estado == PlanState.ACTIVO

    def test_activar_plan_inexistente_lanza_not_found(self, manager):
        with pytest.raises(PlanNotFoundError):
            manager.activar("no-existe")

    def test_activar_dos_veces_el_mismo_plan_falla(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(plan.id)
        with pytest.raises(IllegalPlanTransitionError):
            manager.activar(plan.id)


# ---------------------------------------------------------------------------
# PlannerManager -- estrategias alternativas (§5) y descarte de candidatos
# ---------------------------------------------------------------------------


class TestCandidatos:
    def test_generar_candidatos_nacen_todos_en_creado(self, manager):
        candidatos = manager.generar_candidatos("obj-1", [tasks("cap.a"), tasks("cap.b"), tasks("cap.c")])
        assert len(candidatos) == 3
        assert all(c.estado == PlanState.CREADO for c in candidatos)

    def test_generar_candidatos_cada_uno_con_version_distinta(self, manager):
        candidatos = manager.generar_candidatos("obj-1", [tasks("cap.a"), tasks("cap.b")])
        assert [c.version for c in candidatos] == [1, 2]

    def test_activar_un_candidato_descarta_los_demas(self, manager):
        candidatos = manager.generar_candidatos("obj-1", [tasks("cap.a"), tasks("cap.b"), tasks("cap.c")])
        manager.activar(candidatos[1].id)
        assert manager.obtener(candidatos[0].id).estado == PlanState.OBSOLETO
        assert manager.obtener(candidatos[1].id).estado == PlanState.ACTIVO
        assert manager.obtener(candidatos[2].id).estado == PlanState.OBSOLETO

    def test_descarte_de_candidatos_emite_plan_obsoleto(self, manager, grabador):
        candidatos = manager.generar_candidatos("obj-1", [tasks("cap.a"), tasks("cap.b")])
        manager.activar(candidatos[0].id)
        assert grabador.nombres().count(events.PLAN_OBSOLETO) == 1

    def test_elegir_y_activar_con_criterio_primero(self, manager):
        candidatos = manager.generar_candidatos("obj-1", [tasks("cap.a"), tasks("cap.b")])
        elegido = manager.elegir_y_activar(candidatos, criterio=primero)
        assert elegido.id == candidatos[0].id
        assert manager.obtener(candidatos[0].id).estado == PlanState.ACTIVO

    def test_elegir_y_activar_con_criterio_alternativo(self, manager):
        candidatos = manager.generar_candidatos(
            "obj-1",
            [tasks("cap.a", "cap.b", "cap.c"), tasks("cap.x")],
        )
        elegido = manager.elegir_y_activar(candidatos, criterio=menor_numero_de_tasks)
        assert elegido.id == candidatos[1].id

    def test_seleccionar_rechaza_candidatos_de_objetivos_distintos(self, manager):
        c1 = manager.crear_plan("obj-1", tasks("cap.a"))
        c2 = manager.crear_plan("obj-2", tasks("cap.b"))
        with pytest.raises(InvalidPlanError):
            seleccionar([c1, c2])

    def test_seleccionar_rechaza_candidato_ya_activo(self, manager):
        p1 = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(p1.id)
        p2 = manager.replanificar("obj-1", tasks("cap.b"))
        with pytest.raises(InvalidPlanError):
            seleccionar([p1, p2])

    def test_seleccionar_lista_vacia_falla(self):
        with pytest.raises(InvalidPlanError):
            seleccionar([])


# ---------------------------------------------------------------------------
# PlannerManager -- cancelación (§8)
# ---------------------------------------------------------------------------


class TestCancelacion:
    def test_cancelar_desde_creado(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.cancelar(plan.id)
        assert manager.obtener(plan.id).estado == PlanState.CANCELADO

    def test_cancelar_desde_activo(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(plan.id)
        manager.cancelar(plan.id)
        assert manager.obtener(plan.id).estado == PlanState.CANCELADO

    def test_cancelar_desde_obsoleto(self, manager):
        p1 = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(p1.id)
        p2 = manager.replanificar("obj-1", tasks("cap.b"))
        manager.activar(p2.id)  # p1 -> obsoleto
        manager.cancelar(p1.id)
        assert manager.obtener(p1.id).estado == PlanState.CANCELADO

    def test_cancelado_nunca_vuelve_atras(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.cancelar(plan.id)
        with pytest.raises(IllegalPlanTransitionError):
            manager.activar(plan.id)
        with pytest.raises(IllegalPlanTransitionError):
            manager.cancelar(plan.id)

    def test_cancelar_emite_plan_cancelado(self, manager, grabador):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.cancelar(plan.id)
        assert grabador.nombres()[-1] == events.PLAN_CANCELADO

    def test_cancelar_por_objetivo_cancela_el_plan_activo(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(plan.id)
        cancelado = manager.cancelar_por_objetivo("obj-1")
        assert cancelado.id == plan.id
        assert manager.obtener(plan.id).estado == PlanState.CANCELADO
        assert manager.activo_de("obj-1") is None

    def test_cancelar_por_objetivo_sin_plan_activo_devuelve_none(self, manager):
        manager.crear_plan("obj-1", tasks("cap.a"))  # nunca se activa
        assert manager.cancelar_por_objetivo("obj-1") is None

    def test_cancelar_por_objetivo_sin_ningun_plan_devuelve_none(self, manager):
        assert manager.cancelar_por_objetivo("obj-inexistente") is None

    def test_cancelar_plan_inexistente_lanza_not_found(self, manager):
        with pytest.raises(PlanNotFoundError):
            manager.cancelar("no-existe")


# ---------------------------------------------------------------------------
# PlannerManager -- inmutabilidad e invariantes (§1, §10)
# ---------------------------------------------------------------------------


class TestInvariantes:
    def test_replanificar_no_modifica_el_plan_anterior(self, manager):
        p1 = manager.crear_plan("obj-1", tasks("cap.a"))
        tasks_originales = list(p1.tasks)
        manager.replanificar("obj-1", tasks("cap.b"))
        p1_recargado = manager.obtener(p1.id)
        assert list(p1_recargado.tasks) == tasks_originales

    def test_historial_registra_todas_las_transiciones(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(plan.id)
        manager.cancelar(plan.id)
        eventos_historial = [h["evento"] for h in manager.obtener(plan.id).historial]
        assert eventos_historial == [events.PLAN_CREADO, events.PLAN_ACTIVADO, events.PLAN_CANCELADO]

    def test_actualizado_en_cambia_con_cada_transicion(self, manager):
        plan = manager.crear_plan("obj-1", tasks("cap.a"))
        creado_en = plan.actualizado_en
        manager.activar(plan.id)
        assert manager.obtener(plan.id).actualizado_en != creado_en

    def test_historial_de_devuelve_ordenado_por_version(self, manager):
        manager.crear_plan("obj-1", tasks("cap.a"))
        manager.replanificar("obj-1", tasks("cap.b"))
        manager.reintentar("obj-1", tasks("cap.c"))
        versiones = [p.version for p in manager.historial_de("obj-1")]
        assert versiones == [1, 2, 3]

    def test_invariante_un_activo_no_se_viola_via_api_publica(self, manager):
        """No hay forma, usando solo la API pública de PlannerManager, de dejar
        dos Planes `activo` para el mismo Objetivo (Invariante 2): activar un
        Plan ya deja exactamente uno activo, sin importar cuántas rondas de
        candidatos y replanificaciones hayan ocurrido antes."""
        p1 = manager.crear_plan("obj-1", tasks("cap.a"))
        manager.activar(p1.id)
        candidatos = manager.generar_candidatos("obj-1", [tasks("cap.b"), tasks("cap.c")])
        manager.activar(candidatos[0].id)  # obsoleta a p1 y descarta candidatos[1]
        activos = [p for p in manager.historial_de("obj-1") if p.estado == PlanState.ACTIVO]
        assert len(activos) == 1
        assert activos[0].id == candidatos[0].id

    def test_no_se_puede_reactivar_un_candidato_ya_descartado(self, manager):
        """Un candidato descartado (creado -> obsoleto, §8) es tan terminal
        para `activar` como cualquier otro Plan `obsoleto`: no vuelve a
        `activo` -- eso es lo que garantiza la Invariante 2 sin necesitar una
        excepción dedicada."""
        candidatos = manager.generar_candidatos("obj-1", [tasks("cap.a"), tasks("cap.b")])
        manager.activar(candidatos[0].id)
        with pytest.raises(IllegalPlanTransitionError):
            manager.activar(candidatos[1].id)


# ---------------------------------------------------------------------------
# PlannerStore
# ---------------------------------------------------------------------------


class TestPlannerStore:
    def test_create_rechaza_id_duplicado(self, store):
        plan = Plan(objective_id="obj-1", tasks=tasks("cap.a"))
        store.create(plan)
        with pytest.raises(ValueError):
            store.create(plan)

    def test_update_plan_no_existente_lanza_not_found(self, store):
        plan = Plan(objective_id="obj-1", tasks=tasks("cap.a"))
        with pytest.raises(PlanNotFoundError):
            store.update(plan)

    def test_get_devuelve_none_si_no_existe(self, store):
        assert store.get("no-existe") is None

    def test_by_objective_filtra_correctamente(self, store):
        p1 = Plan(objective_id="obj-1", tasks=tasks("cap.a"))
        p2 = Plan(objective_id="obj-2", tasks=tasks("cap.b"))
        store.create(p1)
        store.create(p2)
        assert [p.id for p in store.by_objective("obj-1")] == [p1.id]
