"""
eon.workspace.workspace
==========================
`WorkspaceManager` (WORKSPACE.md §4.3): único punto de escritura sobre un
Workspace. `crear`, `abrir`, `cerrar`, `recuperar`, `eliminar`, y las
transiciones de estado de §3 -- invocadas exclusivamente por el
Coordinator (§12). Nunca ejecuta Tasks ni interpreta resultados: solo
aplica la transición, la registra en `historial`, refresca el snapshot y
emite el evento correspondiente (§7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from . import events, validators
from .artifacts import ArtifactRegistry
from .exceptions import (
    InvalidWorkspaceError,
    WorkspaceAlreadyExistsError,
    WorkspaceNotFoundError,
    WorkspaceNotTerminalError,
    WorkspaceRecoveryError,
)
from .layout import WorkspaceLayout
from .models import Workspace, WorkspaceState
from .snapshot import WorkspaceSnapshot, cargar_snapshot, guardar_snapshot
from .workspace_store import WorkspaceStore


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class WorkspaceManager:
    def __init__(self, root: str, store: WorkspaceStore, event_bus: Any) -> None:
        self._root = root
        self._store = store
        self._events = event_bus

    # ---- infraestructura interna ----

    def _layout(self, workspace_id: str) -> WorkspaceLayout:
        return WorkspaceLayout(self._root, workspace_id)

    def _require(self, workspace_id: str) -> Workspace:
        ws = self._store.get(workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(workspace_id)
        return ws

    def _guardar_snapshot(self, ws: Workspace) -> WorkspaceSnapshot:
        snap = WorkspaceSnapshot.desde_workspace(ws)
        guardar_snapshot(self._layout(ws.id), snap)
        return snap

    # ---- ciclo de vida (§4.3) ----

    def crear(self, objective_id: str, configuracion: dict | None = None, workspace_id: str | None = None) -> Workspace:
        """Un Objetivo raíz posee como máximo un Workspace no terminal
        (§2, §9.2, Invariante 2). Materializa la estructura fija de §5
        antes de anotar `workspace_creado`."""
        if not objective_id or not str(objective_id).strip():
            raise InvalidWorkspaceError("`crear` requiere un `objective_id` no vacío.")
        for existente in self._store.list():
            if existente.objective_id == objective_id and not existente.es_terminal():
                raise WorkspaceAlreadyExistsError(objective_id)

        kwargs: dict[str, Any] = {"objective_id": objective_id, "configuracion": dict(configuracion or {})}
        if workspace_id:
            kwargs["id"] = workspace_id
        ws = Workspace(**kwargs)

        self._layout(ws.id).materializar()
        ws.registrar_evento(events.WORKSPACE_CREADO, de=None, a=ws.estado.value)
        self._store.create(ws)
        self._guardar_snapshot(ws)
        self._events.emit(events.WORKSPACE_CREADO, workspace_id=ws.id, objective_id=objective_id)
        return ws

    def abrir(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """Recupera un Workspace ya persistido para consulta o
        continuación, sin tocar su `estado`. Cada invocación anota
        exactamente una entrada `workspace_abierto` (§8.4, idempotencia)."""
        ws = self._require(workspace_id)
        ws.registrar_evento(events.WORKSPACE_ABIERTO, de=ws.estado.value, a=ws.estado.value, motivo=motivo)
        self._store.update(ws)
        self._guardar_snapshot(ws)
        self._events.emit(events.WORKSPACE_ABIERTO, workspace_id=ws.id, motivo=motivo)
        return ws

    def cerrar(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """El Workspace deja de aceptar escrituras activas -- no implica
        estado terminal (§7)."""
        ws = self._require(workspace_id)
        ws.registrar_evento(events.WORKSPACE_CERRADO, de=ws.estado.value, a=ws.estado.value, motivo=motivo)
        self._store.update(ws)
        self._guardar_snapshot(ws)
        self._events.emit(events.WORKSPACE_CERRADO, workspace_id=ws.id, motivo=motivo)
        return ws

    def recuperar(self, workspace_id: str) -> Workspace:
        """WORKSPACE.md §8.2: (1) verifica la estructura de directorios,
        (2)/(3) reconstruye a partir del `historial` persistido en el
        `WorkspaceStore` -- nunca de `metadata/`, que es derivada (§6,
        Invariante 13) -- y (4) emite `workspace_abierto`. El snapshot en
        `state/` se usa como referencia de auditoría, no como fuente de
        `estado`."""
        layout = self._layout(workspace_id)
        if not layout.existe():
            raise WorkspaceRecoveryError(workspace_id)
        ws = self._store.get(workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(workspace_id)
        # Referencia de auditoría (§8.2): no se usa para decidir `estado`.
        cargar_snapshot(layout)
        ws.registrar_evento(events.WORKSPACE_ABIERTO, de=ws.estado.value, a=ws.estado.value, motivo="recuperacion")
        self._store.update(ws)
        self._guardar_snapshot(ws)
        self._events.emit(events.WORKSPACE_ABIERTO, workspace_id=ws.id, motivo="recuperacion")
        return ws

    def eliminar(self, workspace_id: str) -> None:
        """Un Workspace en un estado no terminal no puede eliminarse (§9.4,
        Invariante 15)."""
        ws = self._require(workspace_id)
        if not ws.es_terminal():
            raise WorkspaceNotTerminalError(workspace_id)
        self._store.delete(workspace_id)
        self._layout(workspace_id).eliminar()
        self._events.emit(events.WORKSPACE_ELIMINADO, workspace_id=workspace_id)

    # ---- transiciones de estado (§3), invocadas por el Coordinator ----

    def _transicionar(
        self, workspace_id: str, destino: WorkspaceState, motivo: str | None = None, **extra: Any
    ) -> Workspace:
        ws = self._require(workspace_id)
        origen = ws.estado
        evento = validators.validar_transicion(origen, destino)
        ws.estado = destino
        ws.registrar_evento(evento, de=origen.value, a=destino.value, motivo=motivo, **extra)
        self._store.update(ws)
        self._guardar_snapshot(ws)
        self._events.emit(evento, workspace_id=ws.id, de=origen.value, a=destino.value, motivo=motivo, **extra)
        return ws

    def planificar(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """CREATED -> PLANNING, o VERIFYING -> PLANNING (replanificación,
        PLANNER.md §7). Mismo evento en ambos casos (§3, "Regla de
        ciclo")."""
        return self._transicionar(workspace_id, WorkspaceState.PLANNING, motivo)

    def programar(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """PLANNING -> SCHEDULING: existe un Plan `activo` (plan listo,
        PLANNER.md §8)."""
        return self._transicionar(workspace_id, WorkspaceState.SCHEDULING, motivo)

    def ejecutar(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """SCHEDULING -> RUNNING: el Scheduler despachó la primera Task en
        READY hacia un Worker (SCHEDULER.md §9.7)."""
        return self._transicionar(workspace_id, WorkspaceState.RUNNING, motivo)

    def verificar(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """RUNNING -> VERIFYING: todas las Tasks del Plan activo alcanzaron
        un estado terminal de ejecución exitoso."""
        return self._transicionar(workspace_id, WorkspaceState.VERIFYING, motivo)

    def completar(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """VERIFYING -> COMPLETED: el Verifier emitió `APPROVED`."""
        return self._transicionar(workspace_id, WorkspaceState.COMPLETED, motivo)

    def fallar(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """VERIFYING -> FAILED (reintentos agotados tras rechazo) o
        RUNNING -> FAILED (Tasks agotan reintentos sin replanificación
        posible)."""
        return self._transicionar(workspace_id, WorkspaceState.FAILED, motivo)

    def cancelar(self, workspace_id: str, motivo: str | None = None) -> Workspace:
        """Cualquier estado no terminal -> CANCELLED, en el mismo acto en
        que se cancela el Objetivo raíz (OBJECTIVES.md §8)."""
        return self._transicionar(workspace_id, WorkspaceState.CANCELLED, motivo)

    # ---- metadata (§6): trazabilidad, nunca fuente de `estado` ----

    def anotar_metadata(self, workspace_id: str, categoria: str, valor: Any) -> Workspace:
        """Añade una referencia de trazabilidad (Plan, Task, Worker,
        Capability, evento, resultado...) bajo `metadata[categoria]`, sin
        interpretarla (§6, Invariante 13). Idempotente por valor: no
        duplica la misma referencia dos veces."""
        ws = self._require(workspace_id)
        lista = ws.metadata.setdefault(categoria, [])
        if valor not in lista:
            lista.append(valor)
        ws.actualizado_en = _ahora()
        self._store.update(ws)
        self._guardar_snapshot(ws)
        return ws

    # ---- artefactos (§5, §5.1) ----

    def artefactos(self, workspace_id: str) -> ArtifactRegistry:
        """Devuelve el `ArtifactRegistry` de este Workspace, con la
        propiedad de artefactos ya conocida cargada desde `metadata`
        (categoría `artifact_owners`) para sobrevivir a la recuperación."""
        ws = self._require(workspace_id)
        propietarios = dict(ws.metadata.get("artifact_owners", {}))
        registro = ArtifactRegistry(self._layout(workspace_id), propietarios)
        return registro

    def sincronizar_artefactos(self, workspace_id: str, registro: ArtifactRegistry) -> Workspace:
        """Persiste en `metadata` la propiedad de artefactos acumulada en
        `registro`, para que sobreviva a una recuperación posterior."""
        ws = self._require(workspace_id)
        ws.metadata["artifact_owners"] = registro.propietarios
        ws.actualizado_en = _ahora()
        self._store.update(ws)
        self._guardar_snapshot(ws)
        return ws

    # ---- lectura ----

    def obtener(self, workspace_id: str) -> Workspace:
        return self._require(workspace_id)

    def listar(self) -> list[Workspace]:
        return self._store.list()

    def snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        ws = self._require(workspace_id)
        return WorkspaceSnapshot.desde_workspace(ws)
