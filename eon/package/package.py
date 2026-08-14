"""
eon.package.package
=====================
PackageManager — único punto de escritura sobre Packages.

` solicitar(workspace_ref)` crea un Package en `PENDING`.
`construir(package_id, workspace_ref)` transiciona `PENDING → BUILDING → READY|FAILED`.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import events
from .models import Package, PackageState, WorkspaceRef
from .package_store import PackageStore


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class PackageManager:
    """Gestiona el ciclo de vida de Packages."""

    def __init__(self, root: str | Path, store: PackageStore, event_bus: Any) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._store = store
        self._events = event_bus

    def solicitar(
        self,
        workspace_ref: WorkspaceRef,
        configuracion: dict | None = None,
        package_id: str | None = None,
    ) -> Package:
        """Crea un Package en `PENDING` para el Workspace indicado."""
        import uuid as _uuid

        pid = package_id or str(_uuid.uuid4())
        package = Package(
            workspace_id=workspace_ref.workspace_id,
            id=pid,
            artifacts_path=workspace_ref.artifacts_path,
            metadata=dict(configuracion or {}),
        )
        self._store.create(package)
        self._events.emit(events.PACKAGE_SOLICITADO, package_id=package.id)
        return package

    def construir(self, package_id: str, workspace_ref: WorkspaceRef) -> Package:
        """Transiciona `PENDING → BUILDING → READY|FAILED`.

        Copia los artefactos del Workspace al directorio del Package.
        Si no hay artefactos, el Package se marca como `READY` de todos
        modos (un Workspace vacío sigue siendo un resultado válido).
        """
        package = self._store.get(package_id)
        if package is None:
            raise ValueError(f"Package no encontrado: '{package_id}'.")

        # PENDING → BUILDING
        package.estado = PackageState.BUILDING
        package.actualizado_en = _ahora()
        self._store.update(package)
        self._events.emit(events.PACKAGE_CONSTRUYENDO, package_id=package.id)

        try:
            # Copiar artefactos del Workspace al directorio del Package.
            source = Path(workspace_ref.artifacts_path)
            dest = self._root / package.id / "artifacts"
            dest.mkdir(parents=True, exist_ok=True)

            if source.exists():
                for item in source.iterdir():
                    dest_item = dest / item.name
                    if item.is_file():
                        shutil.copy2(item, dest_item)
                    elif item.is_dir():
                        shutil.copytree(item, dest_item, dirs_exist_ok=True)

            package.estado = PackageState.READY
            package.actualizado_en = _ahora()
            self._store.update(package)
            self._events.emit(events.PACKAGE_LISTO, package_id=package.id)
            return package

        except Exception as exc:
            package.estado = PackageState.FAILED
            package.actualizado_en = _ahora()
            self._store.update(package)
            self._events.emit(events.PACKAGE_FALLIDO, package_id=package.id, error=str(exc))
            return package

    def obtener(self, package_id: str) -> Package | None:
        return self._store.get(package_id)
