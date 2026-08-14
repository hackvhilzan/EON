"""
eon.coordinator.coordinator_store
====================================
Persistencia pura de `CoordinatorExecution`: crear, cargar, guardar,
listar, actualizar. Sin lógica de dominio -- la inteligencia vive en
`CoordinatorManager` (mismo patrón que `WorkspaceStore` y `PackageStore`).

Dos implementaciones, mismo criterio de consistencia con el resto del
proyecto (decisión de API: el contrato no fija nombres de métodos ni
firma, solo la responsabilidad de la capa; se reutilizan los mismos
nombres `create/get/update/delete/list` usados en `PackageStore` y
`WorkspaceStore`):

- `InMemoryCoordinatorStore`: para tests y usos que no requieren
  supervivencia entre procesos.
- `FileCoordinatorStore`: persiste cada ejecución como JSON bajo
  `state/execution.json` de su propio directorio (vía `CoordinatorLayout`),
  lo que permite la recuperación íntegra tras un reinicio de proceso
  exigida por la ORDEN MAESTRA ("RECUPERACIÓN"). Ninguna transición se
  considera válida hasta persistirse aquí ("PERSISTENCIA").
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from .exceptions import CoordinatorNotFoundError
from .layout import CoordinatorLayout
from .models import CoordinatorExecution

_ARCHIVO_ENTIDAD = "execution.json"


class CoordinatorStore(ABC):
    @abstractmethod
    def create(self, execution: CoordinatorExecution) -> CoordinatorExecution: ...

    @abstractmethod
    def get(self, execution_id: str) -> CoordinatorExecution | None: ...

    @abstractmethod
    def update(self, execution: CoordinatorExecution) -> CoordinatorExecution: ...

    @abstractmethod
    def delete(self, execution_id: str) -> None: ...

    @abstractmethod
    def list(self) -> list[CoordinatorExecution]: ...


class InMemoryCoordinatorStore(CoordinatorStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executions: dict[str, CoordinatorExecution] = {}

    def create(self, execution: CoordinatorExecution) -> CoordinatorExecution:
        with self._lock:
            if execution.id in self._executions:
                raise CoordinatorNotFoundError(execution.id)  # colisión de id
            self._executions[execution.id] = execution
        return execution

    def get(self, execution_id: str) -> CoordinatorExecution | None:
        return self._executions.get(execution_id)

    def update(self, execution: CoordinatorExecution) -> CoordinatorExecution:
        with self._lock:
            if execution.id not in self._executions:
                raise CoordinatorNotFoundError(execution.id)
            self._executions[execution.id] = execution
        return execution

    def delete(self, execution_id: str) -> None:
        with self._lock:
            if execution_id not in self._executions:
                raise CoordinatorNotFoundError(execution_id)
            del self._executions[execution_id]

    def list(self) -> list[CoordinatorExecution]:
        return list(self._executions.values())


class FileCoordinatorStore(CoordinatorStore):
    """Persistencia en disco, indexada por
    `root/<execution_id>/state/execution.json` a través de
    `CoordinatorLayout` -- ningún acceso a disco ocurre fuera de ese
    componente."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _layout(self, execution_id: str) -> CoordinatorLayout:
        return CoordinatorLayout(self._root, execution_id)

    def _archivo(self, execution_id: str) -> Path:
        return self._layout(execution_id).resolver("state", _ARCHIVO_ENTIDAD)

    def create(self, execution: CoordinatorExecution) -> CoordinatorExecution:
        with self._lock:
            layout = self._layout(execution.id)
            layout.materializar()
            self._escribir(execution)
        return execution

    def get(self, execution_id: str) -> CoordinatorExecution | None:
        layout = self._layout(execution_id)
        if not layout.existe():
            return None
        archivo = self._archivo(execution_id)
        if not archivo.exists():
            return None
        return self._leer(archivo)

    def update(self, execution: CoordinatorExecution) -> CoordinatorExecution:
        with self._lock:
            if not self._archivo(execution.id).exists():
                raise CoordinatorNotFoundError(execution.id)
            self._escribir(execution)
        return execution

    def delete(self, execution_id: str) -> None:
        with self._lock:
            if not self._archivo(execution_id).exists():
                raise CoordinatorNotFoundError(execution_id)
            self._layout(execution_id).eliminar()

    def list(self) -> list[CoordinatorExecution]:
        resultado: list[CoordinatorExecution] = []
        if not self._root.is_dir():
            return resultado
        for entrada in sorted(self._root.iterdir()):
            if entrada.is_dir():
                execution = self.get(entrada.name)
                if execution is not None:
                    resultado.append(execution)
        return resultado

    def _escribir(self, execution: CoordinatorExecution) -> None:
        destino = self._archivo(execution.id)
        tmp = destino.with_suffix(".tmp")
        tmp.write_text(json.dumps(execution.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(destino)

    @staticmethod
    def _leer(archivo: Path) -> CoordinatorExecution:
        data = json.loads(archivo.read_text(encoding="utf-8"))
        return CoordinatorExecution.from_dict(data)
