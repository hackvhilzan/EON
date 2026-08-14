"""
Tests del Objective Engine (Fase 7). No dependen de red ni de LLM real: usan un
EventBus limpio y un InMemoryObjectiveStore, con un ResultadoJudge falso donde
hace falta razonar sobre `criterio_de_exito`.
"""

from __future__ import annotations

import pytest

from eon.objectives import (
    CyclicDependencyError,
    EventBus,
    IllegalTransitionError,
    InMemoryObjectiveStore,
    InvalidDecompositionError,
    InvalidObjectiveError,
    ObjectiveManager,
    ObjectiveNotFoundError,
    ObjectiveOrigin,
    ObjectiveState,
    RetryLimitExceededError,
    TerminalObjectiveError,
    Verifier,
    events,
)
from eon.objectives.dependency_graph import DependencyGraph


class _JudgeFijo:
    """ResultadoJudge determinista para tests: siempre devuelve lo que se le
    configuró, sin importar el criterio_de_exito ni el resultado recibidos."""

    def __init__(self, cumple: bool, confianza: float):
        self.cumple = cumple
        self.confianza = confianza

    def juzgar(self, criterio_de_exito, resultado):
        return self.cumple, self.confianza


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
    return InMemoryObjectiveStore()


@pytest.fixture
def manager(store, bus):
    return ObjectiveManager(store, bus, max_reintentos=2)


def crear(manager, **kwargs):
    defaults = dict(descripcion="hacer algo", criterio_de_exito="algo quedó hecho")
    defaults.update(kwargs)
    return manager.crear_objetivo(**defaults)


# ---------------------------------------------------------------------------
# Creación e invariantes básicos
# ---------------------------------------------------------------------------


class TestCreacion:
    def test_nace_pendiente(self, manager):
        obj = crear(manager)
        assert obj.estado == ObjectiveState.PENDIENTE

    def test_nace_con_evento_objetivo_creado(self, manager, grabador):
        crear(manager)
        assert grabador.nombres() == [events.OBJETIVO_CREADO]

    def test_propietario_por_defecto_es_el_origen(self, manager):
        obj = crear(manager, origen=ObjectiveOrigin.USUARIO)
        assert obj.propietario == "usuario"

    def test_propietario_explicito_no_se_sobreescribe(self, manager):
        obj = crear(manager, propietario="scheduler")
        assert obj.propietario == "scheduler"

    def test_sin_criterio_de_exito_no_es_valido(self, manager):
        with pytest.raises(InvalidObjectiveError):
            crear(manager, criterio_de_exito="")

    def test_dos_objetivos_con_misma_descripcion_son_distintos(self, manager):
        a = crear(manager, descripcion="igual", criterio_de_exito="igual")
        b = crear(manager, descripcion="igual", criterio_de_exito="igual")
        assert a.id != b.id

    def test_confianza_minima_fuera_de_rango_es_invalida(self, manager):
        with pytest.raises(InvalidObjectiveError):
            crear(manager, confianza_minima=1.5)

    def test_padre_id_inexistente_se_rechaza(self, manager):
        with pytest.raises(ObjectiveNotFoundError):
            crear(manager, padre_id="no-existe")

    def test_depende_de_inexistente_se_rechaza(self, manager):
        with pytest.raises(ObjectiveNotFoundError):
            crear(manager, depende_de=["no-existe"])

    def test_depender_de_si_mismo_se_rechaza(self, store):
        # La API pública (crear_objetivo) no puede referenciar el propio id antes
        # de crearlo, así que la autodependencia se prueba directamente sobre
        # DependencyGraph, que es donde vive la validación.
        graph = DependencyGraph(store)
        with pytest.raises(CyclicDependencyError):
            graph.validar_dependencias("mismo-id", ["mismo-id"])


# ---------------------------------------------------------------------------
# Máquina de estados: transiciones legales e ilegales
# ---------------------------------------------------------------------------


class TestTransicionesLegales:
    def test_flujo_feliz_completo(self, manager, grabador):
        obj = crear(manager)
        manager.planificar(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.PLANIFICANDO
        manager.iniciar(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.EN_PROGRESO
        manager.verificar(obj.id, resultado=(True, 0.9))
        assert manager._store.get(obj.id).estado == ObjectiveState.COMPLETADO

        assert grabador.nombres() == [
            events.OBJETIVO_CREADO,
            events.OBJETIVO_PLANIFICANDO,
            events.OBJETIVO_INICIADO,
            events.OBJETIVO_VERIFICANDO,
            events.OBJETIVO_COMPLETADO,
        ]

    def test_pausar_y_reanudar(self, manager):
        obj = crear(manager)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        manager.pausar(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.PAUSADO
        manager.reanudar(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.EN_PROGRESO

    def test_reanudar_cae_a_bloqueado_si_dependencia_ya_no_se_cumple(self, manager):
        dep = crear(manager, descripcion="dependencia")
        obj = crear(manager, descripcion="depende de otro", depende_de=[dep.id])
        manager.planificar(dep.id)
        manager.iniciar(dep.id)
        manager.verificar(dep.id, resultado=(True, 0.9))  # dep completado

        manager.planificar(obj.id)  # dependencias cumplidas -> sigue en planificando
        manager.iniciar(obj.id)
        manager.pausar(obj.id)

        # ahora forzamos que la dependencia deje de estar completada (falla externa)
        dep_obj = manager._store.get(dep.id)
        dep_obj.estado = ObjectiveState.FALLIDO  # simulate: fuera de camino normal, solo para el test
        manager._store.update(dep_obj)

        resultado = manager.reanudar(obj.id)
        assert resultado.estado == ObjectiveState.BLOQUEADO

    def test_planificar_bloquea_si_dependencia_no_completada(self, manager):
        dep = crear(manager, descripcion="dependencia")
        obj = crear(manager, descripcion="depende de otro", depende_de=[dep.id])
        manager.planificar(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.BLOQUEADO

    def test_desbloquear_cuando_dependencia_se_completa(self, manager):
        dep = crear(manager, descripcion="dependencia")
        obj = crear(manager, descripcion="depende de otro", depende_de=[dep.id])
        manager.planificar(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.BLOQUEADO

        manager.planificar(dep.id)
        manager.iniciar(dep.id)
        manager.verificar(dep.id, resultado=(True, 0.9))

        manager.desbloquear(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.PLANIFICANDO

    def test_verificar_con_baja_confianza_va_a_fallando(self, manager):
        obj = crear(manager, confianza_minima=0.8)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        manager.verificar(obj.id, resultado=(True, 0.5))
        assert manager._store.get(obj.id).estado == ObjectiveState.FALLANDO

    def test_verificando_a_fallando_emite_evento(self, manager, grabador):
        obj = crear(manager, confianza_minima=0.9)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        manager.verificar(obj.id, resultado=(False, 0.1))
        assert events.OBJETIVO_FALLANDO in grabador.nombres()


class TestTransicionesIlegales:
    def test_no_se_puede_iniciar_sin_planificar(self, manager):
        obj = crear(manager)
        with pytest.raises(IllegalTransitionError):
            manager.iniciar(obj.id)

    def test_no_se_puede_pausar_desde_pendiente(self, manager):
        obj = crear(manager)
        with pytest.raises(IllegalTransitionError):
            manager.pausar(obj.id)

    def test_no_se_puede_verificar_desde_pendiente(self, manager):
        obj = crear(manager)
        with pytest.raises(IllegalTransitionError):
            manager.verificar(obj.id)

    def test_estados_terminales_no_revierten(self, manager):
        obj = crear(manager)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        manager.verificar(obj.id, resultado=(True, 0.9))
        assert manager._store.get(obj.id).estado == ObjectiveState.COMPLETADO

        for operacion in (manager.pausar, manager.iniciar):
            with pytest.raises(IllegalTransitionError):
                operacion(obj.id)
        with pytest.raises(IllegalTransitionError):
            manager.verificar(obj.id)

    def test_no_se_puede_reasignar_propietario_de_terminal(self, manager):
        obj = crear(manager)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        manager.verificar(obj.id, resultado=(True, 0.9))
        with pytest.raises(TerminalObjectiveError):
            manager.reasignar_propietario(obj.id, "otro")

    def test_operacion_sobre_objetivo_inexistente(self, manager):
        with pytest.raises(ObjectiveNotFoundError):
            manager.planificar("no-existe")

    def test_cancelar_desde_verificando_no_esta_permitido_directamente(self, manager):
        obj = crear(manager)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        # forzamos el estado a verificando sin pasar por verificar() para probar
        # la restricción de validar_cancelacion en aislamiento
        o = manager._store.get(obj.id)
        o.estado = ObjectiveState.VERIFICANDO
        manager._store.update(o)
        with pytest.raises(IllegalTransitionError):
            manager.cancelar(obj.id)


# ---------------------------------------------------------------------------
# Ciclos (dependency_graph)
# ---------------------------------------------------------------------------


class TestCiclos:
    def test_ciclo_transitivo_en_depende_de_se_rechaza(self, manager, store):
        graph = DependencyGraph(store)
        a = crear(manager, descripcion="a")
        b = manager.crear_objetivo(descripcion="b", criterio_de_exito="b hecho", depende_de=[a.id])
        # a depende de b crearía un ciclo a->b->a; se valida contra el grafo ya
        # existente en el store, en vez de crear un tercer objetivo.
        with pytest.raises(CyclicDependencyError):
            graph.validar_dependencias(a.id, [b.id])

    def test_padre_inexistente_se_rechaza_al_crear(self, manager):
        with pytest.raises(ObjectiveNotFoundError):
            crear(manager, padre_id="fantasma")

    def test_reparentar_no_esta_soportado(self, manager, store):
        graph = DependencyGraph(store)
        padre = crear(manager, descripcion="padre")
        hijos = manager.descomponer(
            padre.id,
            [
                {"descripcion": "hijo", "criterio_de_exito": "hijo hecho"},
            ],
        )
        with pytest.raises(Exception):
            graph.validar_reparentado(padre.id, hijos[0].id)


# ---------------------------------------------------------------------------
# Descomposición, jerarquía y completado por composición
# ---------------------------------------------------------------------------


class TestDescomposicion:
    def test_descomponer_crea_hijos_en_pendiente(self, manager):
        padre = crear(manager)
        hijos = manager.descomponer(
            padre.id,
            [
                {"descripcion": "sub 1", "criterio_de_exito": "sub 1 hecho"},
                {"descripcion": "sub 2", "criterio_de_exito": "sub 2 hecho"},
            ],
        )
        assert len(hijos) == 2
        assert all(h.estado == ObjectiveState.PENDIENTE for h in hijos)
        assert all(h.padre_id == padre.id for h in hijos)
        assert all(h.origen == ObjectiveOrigin.DESCOMPOSICION for h in hijos)

    def test_descomponer_no_cambia_estado_del_padre(self, manager):
        padre = crear(manager)
        manager.descomponer(padre.id, [{"descripcion": "sub", "criterio_de_exito": "hecho"}])
        assert manager._store.get(padre.id).estado == ObjectiveState.PENDIENTE

    def test_descomponer_emite_evento_estructural(self, manager, grabador):
        padre = crear(manager)
        manager.descomponer(padre.id, [{"descripcion": "sub", "criterio_de_exito": "hecho"}])
        nombres = grabador.nombres()
        assert events.OBJETIVO_DESCOMPUESTO in nombres
        # crear_objetivo del padre + crear_objetivo del hijo + descompuesto
        assert nombres.count(events.OBJETIVO_CREADO) == 2

    def test_descomponer_sin_sub_objetivos_falla(self, manager):
        padre = crear(manager)
        with pytest.raises(InvalidDecompositionError):
            manager.descomponer(padre.id, [])

    def test_completado_por_composicion_de_hijos_obligatorios(self, manager):
        padre = crear(manager)
        hijos = manager.descomponer(
            padre.id,
            [
                {"descripcion": "sub 1", "criterio_de_exito": "hecho 1"},
                {"descripcion": "sub 2", "criterio_de_exito": "hecho 2", "obligatorio": False},
            ],
        )
        obligatorio, opcional = hijos

        for hijo_id in (obligatorio.id,):
            manager.planificar(hijo_id)
            manager.iniciar(hijo_id)
            manager.verificar(hijo_id, resultado=(True, 0.9))

        manager.planificar(padre.id)
        manager.iniciar(padre.id)
        resultado = manager.verificar(padre.id)
        # el opcional sigue pendiente pero no bloquea el completado del padre
        assert resultado.estado == ObjectiveState.COMPLETADO

    def test_no_completa_si_falta_hijo_obligatorio(self, manager):
        padre = crear(manager)
        manager.descomponer(
            padre.id,
            [
                {"descripcion": "sub 1", "criterio_de_exito": "hecho 1"},
            ],
        )
        manager.planificar(padre.id)
        manager.iniciar(padre.id)
        resultado = manager.verificar(padre.id)
        assert resultado.estado == ObjectiveState.FALLANDO


# ---------------------------------------------------------------------------
# Cancelación y pausa recursivas
# ---------------------------------------------------------------------------


class TestCascadas:
    def _arbol(self, manager):
        raiz = crear(manager, descripcion="raiz")
        hijos = manager.descomponer(
            raiz.id,
            [
                {"descripcion": "hijo 1", "criterio_de_exito": "hecho"},
                {"descripcion": "hijo 2", "criterio_de_exito": "hecho"},
            ],
        )
        nietos = manager.descomponer(
            hijos[0].id,
            [
                {"descripcion": "nieto 1", "criterio_de_exito": "hecho"},
            ],
        )
        return raiz, hijos, nietos

    def test_cancelar_cascada_a_todo_el_subarbol(self, manager):
        raiz, hijos, nietos = self._arbol(manager)
        manager.cancelar(raiz.id)

        assert manager._store.get(raiz.id).estado == ObjectiveState.CANCELADO
        for h in hijos:
            assert manager._store.get(h.id).estado == ObjectiveState.CANCELADO
        for n in nietos:
            assert manager._store.get(n.id).estado == ObjectiveState.CANCELADO

    def test_cancelar_no_reabre_un_hijo_ya_completado(self, manager):
        raiz, hijos, _ = self._arbol(manager)
        # completamos el hijo 2 (sin sub-objetivos propios)
        manager.planificar(hijos[1].id)
        manager.iniciar(hijos[1].id)
        manager.verificar(hijos[1].id, resultado=(True, 0.9))
        assert manager._store.get(hijos[1].id).estado == ObjectiveState.COMPLETADO

        manager.cancelar(raiz.id)
        # un estado terminal no revierte: sigue completado, no pasa a cancelado
        assert manager._store.get(hijos[1].id).estado == ObjectiveState.COMPLETADO
        assert manager._store.get(raiz.id).estado == ObjectiveState.CANCELADO

    def test_pausar_cascada_a_todo_el_subarbol_no_terminal(self, manager):
        raiz, hijos, nietos = self._arbol(manager)
        manager.planificar(raiz.id)
        manager.iniciar(raiz.id)

        manager.pausar(raiz.id)

        assert manager._store.get(raiz.id).estado == ObjectiveState.PAUSADO
        for h in hijos:
            assert manager._store.get(h.id).estado == ObjectiveState.PAUSADO
        for n in nietos:
            assert manager._store.get(n.id).estado == ObjectiveState.PAUSADO

    def test_cascada_marca_historial_y_emite_eventos_por_cada_hijo(self, manager, grabador):
        raiz, hijos, nietos = self._arbol(manager)
        manager.cancelar(raiz.id)

        eventos_cancelado = [e for e in grabador.recibidos if e[0] == events.OBJETIVO_CANCELADO]
        # raiz + 2 hijos + 1 nieto = 4 cancelaciones
        assert len(eventos_cancelado) == 4

        nieto = manager._store.get(nietos[0].id)
        ultima_entrada = nieto.historial[-1]
        assert ultima_entrada["evento"] == events.OBJETIVO_CANCELADO
        assert ultima_entrada["extra"]["cascada"] is True


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------


class TestHistorial:
    def test_toda_transicion_queda_en_historial(self, manager):
        obj = crear(manager)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        manager.verificar(obj.id, resultado=(True, 0.9))

        historial = manager._store.get(obj.id).historial
        eventos = [h["evento"] for h in historial]
        assert eventos == [
            events.OBJETIVO_CREADO,
            events.OBJETIVO_PLANIFICANDO,
            events.OBJETIVO_INICIADO,
            events.OBJETIVO_VERIFICANDO,
            events.OBJETIVO_COMPLETADO,
        ]

    def test_historial_registra_de_y_a(self, manager):
        obj = crear(manager)
        manager.planificar(obj.id)
        entrada = manager._store.get(obj.id).historial[-1]
        assert entrada["de"] == "pendiente"
        assert entrada["a"] == "planificando"

    def test_reasignar_propietario_queda_en_historial_sin_cambiar_estado(self, manager):
        obj = crear(manager)
        estado_antes = manager._store.get(obj.id).estado
        manager.reasignar_propietario(obj.id, "scheduler", motivo="propietario inactivo")
        actualizado = manager._store.get(obj.id)
        assert actualizado.estado == estado_antes
        assert actualizado.propietario == "scheduler"
        assert actualizado.historial[-1]["evento"] == events.OBJETIVO_REASIGNADO


# ---------------------------------------------------------------------------
# Reintentos
# ---------------------------------------------------------------------------


class TestReintentos:
    def _llevar_a_fallando(self, manager, obj_id):
        manager.planificar(obj_id)
        manager.iniciar(obj_id)
        manager.verificar(obj_id, resultado=(False, 0.1))
        assert manager._store.get(obj_id).estado == ObjectiveState.FALLANDO

    def test_reintentar_vuelve_a_en_progreso_dentro_del_limite(self, manager):
        obj = crear(manager)
        self._llevar_a_fallando(manager, obj.id)
        resultado = manager.reintentar(obj.id)
        assert resultado.estado == ObjectiveState.EN_PROGRESO
        assert manager.reintentos_usados(obj.id) == 1

    def test_reintentar_agota_limite_y_pasa_a_fallido(self, manager):
        obj = crear(manager)
        manager._max_reintentos = 1

        self._llevar_a_fallando(manager, obj.id)
        manager.reintentar(obj.id)  # 1er reintento: vuelve a en_progreso, mismo Objetivo
        manager.verificar(obj.id, resultado=(False, 0.1))  # el nuevo plan también falla
        with pytest.raises(RetryLimitExceededError):  # límite agotado
            manager.reintentar(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.FALLIDO

    def test_fallido_es_terminal_no_revierte(self, manager):
        obj = crear(manager)
        manager._max_reintentos = 0
        self._llevar_a_fallando(manager, obj.id)
        with pytest.raises(RetryLimitExceededError):
            manager.reintentar(obj.id)
        assert manager._store.get(obj.id).estado == ObjectiveState.FALLIDO
        with pytest.raises(IllegalTransitionError):
            manager.reintentar(obj.id)


# ---------------------------------------------------------------------------
# Verificación / Verifier
# ---------------------------------------------------------------------------


class TestVerificacion:
    def test_verifier_con_judge_inyectado(self, store, bus):
        judge = _JudgeFijo(cumple=True, confianza=0.95)
        manager = ObjectiveManager(store, bus, verifier=Verifier(judge))
        obj = crear(manager)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        resultado = manager.verificar(obj.id, resultado={"algo": "producido"})
        assert resultado.estado == ObjectiveState.COMPLETADO

    def test_verifier_sin_judge_ni_resultado_no_completa_por_defecto(self, manager):
        obj = crear(manager)
        manager.planificar(obj.id)
        manager.iniciar(obj.id)
        resultado = manager.verificar(obj.id)  # sin resultado
        assert resultado.estado == ObjectiveState.FALLANDO

    def test_dependencia_fallida_impide_completar(self, manager):
        dep = crear(manager, descripcion="dependencia")
        obj = crear(manager, descripcion="depende de otro", depende_de=[dep.id])

        manager.planificar(dep.id)
        manager.iniciar(dep.id)
        manager.verificar(dep.id, resultado=(False, 0.1))
        manager.reintentar(dep.id)
        manager._max_reintentos = 0
        manager.verificar(dep.id, resultado=(False, 0.1))
        with pytest.raises(RetryLimitExceededError):
            manager.reintentar(dep.id)
        assert manager._store.get(dep.id).estado == ObjectiveState.FALLIDO

        # forzamos manualmente que 'obj' llegue a en_progreso para poder verificar
        o = manager._store.get(obj.id)
        o.estado = ObjectiveState.EN_PROGRESO
        manager._store.update(o)

        resultado = manager.verificar(obj.id, resultado=(True, 0.9))
        assert resultado.estado == ObjectiveState.FALLANDO
        assert resultado.historial[-1]["motivo"] == "dependencia no satisfecha"
