"""Tests del Workspace (Fase 11). Auto-contenidos: EventBus propio, sin
tocar eon.objectives, eon.planner, eon.scheduler, eon.workers ni
eon.verifier (WORKSPACE.md §0)."""

from __future__ import annotations

from pathlib import Path

import pytest
from eon.workspace import (
    SUBDIRECTORIOS,
    ArtifactOwnershipError,
    FileWorkspaceStore,
    IllegalWorkspaceTransitionError,
    InMemoryWorkspaceStore,
    InvalidWorkspaceError,
    Workspace,
    WorkspaceAlreadyExistsError,
    WorkspaceLayout,
    WorkspaceManager,
    WorkspaceNotFoundError,
    WorkspaceNotTerminalError,
    WorkspacePathEscapeError,
    WorkspaceRecoveryError,
    WorkspaceState,
    events,
)


class EventBus:
    def __init__(self):
        self._subs: dict[str, list] = {}

    def on(self, nombre, callback):
        self._subs.setdefault(nombre, []).append(callback)

    def emit(self, nombre, **data):
        for callback in self._subs.get(nombre, []):
            callback(**data)


class _Grabador:
    def __init__(self, bus: EventBus):
        self.recibidos: list[tuple[str, dict]] = []
        for nombre in events.TODOS:
            bus.on(nombre, self._make(nombre))

    def _make(self, nombre):
        def _cb(**data):
            self.recibidos.append((nombre, data))

        return _cb

    def nombres(self):
        return [n for n, _ in self.recibidos]


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def grabador(bus):
    return _Grabador(bus)


@pytest.fixture
def tmp_root(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture(params=["memoria", "disco"])
def store(request, tmp_root):
    if request.param == "memoria":
        return InMemoryWorkspaceStore()
    return FileWorkspaceStore(tmp_root)


@pytest.fixture
def manager(tmp_root, store, bus):
    return WorkspaceManager(tmp_root, store, bus)


def _hasta_verifying(manager, ws_id):
    manager.planificar(ws_id)
    manager.programar(ws_id)
    manager.ejecutar(ws_id)
    return manager.verificar(ws_id)


# ---------------------------------------------------------------------------
# Creación
# ---------------------------------------------------------------------------


class TestCreacion:
    def test_crear_devuelve_workspace_en_created(self, manager):
        ws = manager.crear("obj-1")
        assert ws.estado == WorkspaceState.CREATED
        assert ws.objective_id == "obj-1"

    def test_crear_materializa_estructura_fija(self, manager, tmp_root):
        ws = manager.crear("obj-1")
        raiz = Path(tmp_root) / ws.id
        for sub in SUBDIRECTORIOS:
            assert (raiz / sub).is_dir()

    def test_crear_no_crea_subcarpetas_adicionales(self, manager, tmp_root):
        ws = manager.crear("obj-1")
        raiz = Path(tmp_root) / ws.id
        assert {p.name for p in raiz.iterdir()} == set(SUBDIRECTORIOS)

    def test_crear_emite_workspace_creado(self, manager, grabador):
        ws = manager.crear("obj-1")
        assert grabador.nombres() == [events.WORKSPACE_CREADO]
        assert ws.historial[-1]["evento"] == events.WORKSPACE_CREADO
        assert ws.historial[-1]["de"] is None

    def test_crear_sin_objective_id_falla(self, manager):
        with pytest.raises(InvalidWorkspaceError):
            manager.crear("")

    def test_doble_workspace_no_terminal_para_mismo_objetivo_falla(self, manager):
        manager.crear("obj-1")
        with pytest.raises(WorkspaceAlreadyExistsError):
            manager.crear("obj-1")

    def test_nuevo_workspace_permitido_si_el_anterior_es_terminal(self, manager):
        ws1 = manager.crear("obj-1")
        manager.cancelar(ws1.id)
        ws2 = manager.crear("obj-1")
        assert ws2.id != ws1.id
        assert ws2.estado == WorkspaceState.CREATED


# ---------------------------------------------------------------------------
# Transiciones válidas / inválidas
# ---------------------------------------------------------------------------


class TestTransiciones:
    def test_ciclo_feliz_completo(self, manager, grabador):
        ws = manager.crear("obj-1")
        manager.planificar(ws.id)
        manager.programar(ws.id)
        manager.ejecutar(ws.id)
        manager.verificar(ws.id)
        final = manager.completar(ws.id)
        assert final.estado == WorkspaceState.COMPLETED
        assert grabador.nombres() == [
            events.WORKSPACE_CREADO,
            events.WORKSPACE_PLANIFICANDO,
            events.WORKSPACE_SCHEDULING,
            events.WORKSPACE_EJECUTANDO,
            events.WORKSPACE_VERIFICANDO,
            events.WORKSPACE_COMPLETADO,
        ]

    def test_replanificacion_verifying_a_planning(self, manager):
        ws = manager.crear("obj-1")
        _hasta_verifying(manager, ws.id)
        vuelto = manager.planificar(ws.id, motivo="rechazo del verifier")
        assert vuelto.estado == WorkspaceState.PLANNING

    def test_verifying_a_failed_por_reintentos_agotados(self, manager):
        ws = manager.crear("obj-1")
        _hasta_verifying(manager, ws.id)
        fallido = manager.fallar(ws.id)
        assert fallido.estado == WorkspaceState.FAILED

    def test_running_a_failed(self, manager):
        ws = manager.crear("obj-1")
        manager.planificar(ws.id)
        manager.programar(ws.id)
        manager.ejecutar(ws.id)
        fallido = manager.fallar(ws.id)
        assert fallido.estado == WorkspaceState.FAILED

    @pytest.mark.parametrize(
        "origen_estado",
        [
            WorkspaceState.CREATED,
            WorkspaceState.PLANNING,
            WorkspaceState.SCHEDULING,
            WorkspaceState.RUNNING,
            WorkspaceState.VERIFYING,
        ],
    )
    def test_cancelar_desde_cualquier_no_terminal(self, manager, origen_estado):
        ws = manager.crear("obj-1")
        pasos = {
            WorkspaceState.CREATED: [],
            WorkspaceState.PLANNING: [manager.planificar],
            WorkspaceState.SCHEDULING: [manager.planificar, manager.programar],
            WorkspaceState.RUNNING: [manager.planificar, manager.programar, manager.ejecutar],
            WorkspaceState.VERIFYING: [manager.planificar, manager.programar, manager.ejecutar, manager.verificar],
        }
        for paso in pasos[origen_estado]:
            paso(ws.id)
        cancelado = manager.cancelar(ws.id, motivo="usuario")
        assert cancelado.estado == WorkspaceState.CANCELLED

    def test_saltar_planning_o_scheduling_es_ilegal(self, manager):
        ws = manager.crear("obj-1")
        with pytest.raises(IllegalWorkspaceTransitionError):
            manager.ejecutar(ws.id)

    def test_created_directo_a_running_es_ilegal(self, manager):
        ws = manager.crear("obj-1")
        with pytest.raises(IllegalWorkspaceTransitionError):
            manager._transicionar(ws.id, WorkspaceState.RUNNING)

    @pytest.mark.parametrize("finalizar", ["completar", "cancelar"])
    def test_estados_terminales_no_revierten(self, manager, finalizar):
        ws = manager.crear("obj-1")
        if finalizar == "completar":
            _hasta_verifying(manager, ws.id)
            manager.completar(ws.id)
        else:
            manager.cancelar(ws.id)
        with pytest.raises(IllegalWorkspaceTransitionError):
            manager.planificar(ws.id)
        with pytest.raises(IllegalWorkspaceTransitionError):
            manager.cancelar(ws.id)

    def test_transicion_ilegal_no_muta_estado_ni_historial(self, manager):
        ws = manager.crear("obj-1")
        largo_historial = len(ws.historial)
        with pytest.raises(IllegalWorkspaceTransitionError):
            manager.ejecutar(ws.id)
        recargado = manager.obtener(ws.id)
        assert recargado.estado == WorkspaceState.CREATED
        assert len(recargado.historial) == largo_historial


# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------


class TestEventos:
    def test_ninguna_transicion_sin_evento(self, manager, grabador):
        ws = manager.crear("obj-1")
        manager.planificar(ws.id)
        # tantos eventos como entradas de historial hasta ahora
        assert len(grabador.recibidos) == len(manager.obtener(ws.id).historial)

    def test_abrir_emite_workspace_abierto_una_vez_por_invocacion(self, manager, grabador):
        ws = manager.crear("obj-1")
        grabador.recibidos.clear()
        manager.abrir(ws.id)
        assert grabador.nombres() == [events.WORKSPACE_ABIERTO]
        manager.abrir(ws.id)
        assert grabador.nombres() == [events.WORKSPACE_ABIERTO, events.WORKSPACE_ABIERTO]

    def test_cerrar_no_cambia_estado(self, manager):
        ws = manager.crear("obj-1")
        cerrado = manager.cerrar(ws.id, motivo="pausa")
        assert cerrado.estado == WorkspaceState.CREATED
        assert cerrado.historial[-1]["evento"] == events.WORKSPACE_CERRADO

    def test_eliminar_emite_workspace_eliminado(self, manager, grabador):
        ws = manager.crear("obj-1")
        manager.cancelar(ws.id)
        grabador.recibidos.clear()
        manager.eliminar(ws.id)
        assert grabador.nombres() == [events.WORKSPACE_ELIMINADO]


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------


class TestHistorial:
    def test_historial_es_append_only_y_ordenado(self, manager):
        ws = manager.crear("obj-1")
        manager.planificar(ws.id)
        manager.programar(ws.id)
        final = manager.obtener(ws.id)
        eventos = [h["evento"] for h in final.historial]
        assert eventos == [events.WORKSPACE_CREADO, events.WORKSPACE_PLANIFICANDO, events.WORKSPACE_SCHEDULING]

    def test_historial_registra_de_y_a(self, manager):
        ws = manager.crear("obj-1")
        manager.planificar(ws.id)
        entrada = manager.obtener(ws.id).historial[-1]
        assert entrada["de"] == "created"
        assert entrada["a"] == "planning"


# ---------------------------------------------------------------------------
# WorkspaceLayout
# ---------------------------------------------------------------------------


class TestWorkspaceLayout:
    def test_resolver_dentro_de_artifacts(self, tmp_root):
        layout = WorkspaceLayout(tmp_root, "ws-1")
        layout.materializar()
        ruta = layout.resolver("artifacts", "backend", "main.py")
        assert str(ruta).startswith(str(layout.root / "artifacts"))

    def test_resolver_rechaza_escape_del_root(self, tmp_root):
        layout = WorkspaceLayout(tmp_root, "ws-1")
        layout.materializar()
        with pytest.raises(WorkspacePathEscapeError):
            layout.resolver("artifacts", "..", "..", "etc", "passwd")

    def test_resolver_rechaza_subcarpeta_desconocida(self, tmp_root):
        layout = WorkspaceLayout(tmp_root, "ws-1")
        layout.materializar()
        with pytest.raises(WorkspacePathEscapeError):
            layout.resolver("bin", "algo")

    def test_existe_falso_si_falta_una_subcarpeta(self, tmp_root):
        layout = WorkspaceLayout(tmp_root, "ws-1")
        layout.materializar()
        (layout.root / "cache").rmdir()
        assert layout.existe() is False


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


class TestSnapshots:
    def test_snapshot_refleja_estado_actual(self, manager):
        ws = manager.crear("obj-1")
        manager.planificar(ws.id)
        snap = manager.snapshot(ws.id)
        assert snap.estado == "planning"
        assert snap.workspace_id == ws.id
        assert len(snap.historial) == 2

    def test_snapshot_persistido_en_state(self, manager, tmp_root):
        ws = manager.crear("obj-1")
        archivo = Path(tmp_root) / ws.id / "state" / "snapshot.json"
        assert archivo.exists()


# ---------------------------------------------------------------------------
# Persistencia y recuperación tras reinicio
# ---------------------------------------------------------------------------


class TestRecuperacion:
    def test_recuperar_reconstruye_estado_desde_historial(self, tmp_root, bus):
        store1 = FileWorkspaceStore(tmp_root)
        manager1 = WorkspaceManager(tmp_root, store1, bus)
        ws = manager1.crear("obj-1")
        manager1.planificar(ws.id)
        manager1.programar(ws.id)

        # Simula un reinicio de proceso: store y manager nuevos sobre el
        # mismo `root` en disco.
        store2 = FileWorkspaceStore(tmp_root)
        manager2 = WorkspaceManager(tmp_root, store2, bus)
        recuperado = manager2.recuperar(ws.id)

        assert recuperado.estado == WorkspaceState.SCHEDULING
        # crear, planificar, programar + el propio workspace_abierto de recuperar()
        assert len(recuperado.historial) == 4

    def test_recuperar_sin_estructura_falla(self, tmp_root, bus):
        store = FileWorkspaceStore(tmp_root)
        manager = WorkspaceManager(tmp_root, store, bus)
        with pytest.raises(WorkspaceRecoveryError):
            manager.recuperar("no-existe")

    def test_recuperar_emite_workspace_abierto(self, tmp_root, bus, grabador):
        store = FileWorkspaceStore(tmp_root)
        manager = WorkspaceManager(tmp_root, store, bus)
        ws = manager.crear("obj-1")
        grabador.recibidos.clear()
        manager.recuperar(ws.id)
        assert grabador.nombres() == [events.WORKSPACE_ABIERTO]

    def test_recuperar_no_depende_de_metadata_para_el_estado(self, tmp_root, bus):
        store = FileWorkspaceStore(tmp_root)
        manager = WorkspaceManager(tmp_root, store, bus)
        ws = manager.crear("obj-1")
        manager.planificar(ws.id)
        manager.anotar_metadata(ws.id, "plan_versions", "plan-v1")

        # Corromper la metadata directamente en el store no debe afectar
        # la reconstrucción del `estado`, que depende solo de `historial`.
        crudo = manager.obtener(ws.id)
        crudo.metadata["plan_versions"] = ["algo-completamente-distinto"]
        store.update(crudo)

        recuperado = manager.recuperar(ws.id)
        assert recuperado.estado == WorkspaceState.PLANNING

    def test_file_store_get_no_encontrado_devuelve_none(self, tmp_root):
        store = FileWorkspaceStore(tmp_root)
        assert store.get("no-existe") is None

    def test_file_store_update_no_encontrado_falla(self, tmp_root):
        store = FileWorkspaceStore(tmp_root)
        ws = Workspace(objective_id="obj-1")
        with pytest.raises(WorkspaceNotFoundError):
            store.update(ws)


# ---------------------------------------------------------------------------
# Invariantes del contrato
# ---------------------------------------------------------------------------


class TestInvariantes:
    def test_eliminar_workspace_no_terminal_falla(self, manager):
        ws = manager.crear("obj-1")
        with pytest.raises(WorkspaceNotTerminalError):
            manager.eliminar(ws.id)

    def test_eliminar_workspace_terminal_permite_borrar(self, manager):
        ws = manager.crear("obj-1")
        manager.cancelar(ws.id)
        manager.eliminar(ws.id)
        with pytest.raises(WorkspaceNotFoundError):
            manager.obtener(ws.id)

    def test_workspace_inmutable_salvo_campos_permitidos(self, manager):
        ws = manager.crear("obj-1")
        objective_id_original = ws.objective_id
        creado_en_original = ws.creado_en
        manager.planificar(ws.id)
        recargado = manager.obtener(ws.id)
        assert recargado.objective_id == objective_id_original
        assert recargado.creado_en == creado_en_original
        assert recargado.id == ws.id

    def test_ningun_componente_fuera_del_manager_escribe_estado(self, manager):
        ws = manager.crear("obj-1")
        # El propio dataclass no impide la escritura directa en Python,
        # pero el flujo normativo pasa siempre por WorkspaceManager: se
        # comprueba que las transiciones ilegales se detectan igualmente
        # si alguien manipula el estado a mano y luego usa el Manager.
        ws.estado = WorkspaceState.RUNNING
        with pytest.raises(IllegalWorkspaceTransitionError):
            manager._transicionar(ws.id, WorkspaceState.COMPLETED)


# ---------------------------------------------------------------------------
# Propiedad de artefactos (§5.1)
# ---------------------------------------------------------------------------


class TestArtefactos:
    def test_worker_escribe_y_es_propietario(self, manager):
        ws = manager.crear("obj-1")
        registro = manager.artefactos(ws.id)
        registro.escribir("worker-a", "backend/main.py", b"print(1)")
        assert registro.propietarios["backend/main.py"] == "worker-a"

    def test_otro_worker_no_puede_sobrescribir_sin_autorizacion(self, manager):
        ws = manager.crear("obj-1")
        registro = manager.artefactos(ws.id)
        registro.escribir("worker-a", "backend/main.py", b"print(1)")
        with pytest.raises(ArtifactOwnershipError):
            registro.escribir("worker-b", "backend/main.py", b"print(2)")

    def test_sobrescritura_autorizada_permitida(self, manager):
        ws = manager.crear("obj-1")
        registro = manager.artefactos(ws.id)
        registro.escribir("worker-a", "backend/main.py", b"print(1)")
        registro.escribir("worker-b", "backend/main.py", b"print(2)", autorizado=True)
        assert registro.leer("backend/main.py") == b"print(2)"

    def test_lectura_no_esta_restringida(self, manager):
        ws = manager.crear("obj-1")
        registro = manager.artefactos(ws.id)
        registro.escribir("worker-a", "docs/readme.md", b"hola")
        assert registro.leer("docs/readme.md") == b"hola"

    def test_eliminar_ajeno_sin_autorizacion_falla(self, manager):
        ws = manager.crear("obj-1")
        registro = manager.artefactos(ws.id)
        registro.escribir("worker-a", "docs/readme.md", b"hola")
        with pytest.raises(ArtifactOwnershipError):
            registro.eliminar("worker-b", "docs/readme.md")

    def test_propiedad_sobrevive_a_sincronizar_y_recuperar(self, tmp_root, bus):
        store = FileWorkspaceStore(tmp_root)
        manager = WorkspaceManager(tmp_root, store, bus)
        ws = manager.crear("obj-1")
        registro = manager.artefactos(ws.id)
        registro.escribir("worker-a", "backend/main.py", b"print(1)")
        manager.sincronizar_artefactos(ws.id, registro)

        recuperado_registro = manager.artefactos(ws.id)
        assert recuperado_registro.propietarios["backend/main.py"] == "worker-a"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_anotar_metadata_es_idempotente_por_valor(self, manager):
        ws = manager.crear("obj-1")
        manager.anotar_metadata(ws.id, "task_ids", "task-1")
        manager.anotar_metadata(ws.id, "task_ids", "task-1")
        manager.anotar_metadata(ws.id, "task_ids", "task-2")
        recargado = manager.obtener(ws.id)
        assert recargado.metadata["task_ids"] == ["task-1", "task-2"]
