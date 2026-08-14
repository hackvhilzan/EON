"""
eon.workspace.workspace_store
================================
Persistencia pura de `Workspace`: crear, leer, actualizar, listar, borrar.
Sin lógica de dominio -- la inteligencia vive en `WorkspaceManager`, mismo
patrón que `WorkerStore` (Fase 10) y `PlannerStore`/`SchedulerStore`.

Dos implementaciones:

- `InMemoryWorkspaceStore`: para tests y usos que no requieren
  supervivencia entre procesos.
- `FileWorkspaceStore`: persiste cada Workspace como JSON bajo
  `state/workspace.json` de su propio directorio (vía `WorkspaceLayout`),
  lo que permite la recuperación íntegra tras un reinicio de proceso
  exigida por WORKSPACE.md §8.1. Ninguna transición se considera válida
  hasta persistirse aquí (§8.3).
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from .exceptions import WorkspaceNotFoundError
from .layout import WorkspaceLayout
from .models import Workspace

_ARCHIVO_ENTIDAD = "workspace.json"


class WorkspaceStore(ABC):
    @abstractmethod
    def create(self, workspace: Workspace) -> Workspace: ...

    @abstractmethod
    def get(self, workspace_id: str) -> Workspace | None: ...

    @abstractmethod
    def update(self, workspace: Workspace) -> Workspace: ...

    @abstractmethod
    def delete(self, workspace_id: str) -> None: ...

    @abstractmethod
    def list(self) -> list[Workspace]: ...


class InMemoryWorkspaceStore(WorkspaceStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workspaces: dict[str, Workspace] = {}

    def create(self, workspace: Workspace) -> Workspace:
        with self._lock:
            if workspace.id in self._workspaces:
                raise WorkspaceNotFoundError(workspace.id)  # id ya en uso: colisión de UUID
            self._workspaces[workspace.id] = workspace
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    def update(self, workspace: Workspace) -> Workspace:
        with self._lock:
            if workspace.id not in self._workspaces:
                raise WorkspaceNotFoundError(workspace.id)
            self._workspaces[workspace.id] = workspace
        return workspace

    def delete(self, workspace_id: str) -> None:
        with self._lock:
            if workspace_id not in self._workspaces:
                raise WorkspaceNotFoundError(workspace_id)
            del self._workspaces[workspace_id]

    def list(self) -> list[Workspace]:
        return list(self._workspaces.values())


class FileWorkspaceStore(WorkspaceStore):
    """Persistencia en disco, indexada por `root/<workspace_id>/state/workspace.json`
    a través de `WorkspaceLayout` -- ningún acceso a disco ocurre fuera de
    ese componente (§4.4)."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _layout(self, workspace_id: str) -> WorkspaceLayout:
        return WorkspaceLayout(self._root, workspace_id)

    def _archivo(self, workspace_id: str) -> Path:
        return self._layout(workspace_id).resolver("state", _ARCHIVO_ENTIDAD)

    def create(self, workspace: Workspace) -> Workspace:
        with self._lock:
            layout = self._layout(workspace.id)
            layout.materializar()
            self._escribir(workspace)
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        layout = self._layout(workspace_id)
        if not layout.existe():
            return None
        archivo = self._archivo(workspace_id)
        if not archivo.exists():
            return None
        return self._leer(archivo)

    def update(self, workspace: Workspace) -> Workspace:
        with self._lock:
            if not self._archivo(workspace.id).exists():
                raise WorkspaceNotFoundError(workspace.id)
            self._escribir(workspace)
        return workspace

    def delete(self, workspace_id: str) -> None:
        with self._lock:
            if not self._archivo(workspace_id).exists():
                raise WorkspaceNotFoundError(workspace_id)
            self._layout(workspace_id).eliminar()

    def list(self) -> list[Workspace]:
        resultado: list[Workspace] = []
        if not self._root.is_dir():
            return resultado
        for entrada in sorted(self._root.iterdir()):
            if entrada.is_dir():
                ws = self.get(entrada.name)
                if ws is not None:
                    resultado.append(ws)
        return resultado

    def _escribir(self, workspace: Workspace) -> None:
        destino = self._archivo(workspace.id)
        tmp = destino.with_suffix(".tmp")
        tmp.write_text(json.dumps(workspace.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(destino)

    @staticmethod
    def _leer(archivo: Path) -> Workspace:
        data = json.loads(archivo.read_text(encoding="utf-8"))
        return Workspace.from_dict(data)
