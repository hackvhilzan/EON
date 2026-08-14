"""
eon.workspace.layout
=======================
`WorkspaceLayout` es el único componente que construye rutas de archivo
para un Workspace (WORKSPACE.md §4.4). Materializa y valida la estructura
fija de §5 y rechaza, en un único punto, cualquier ruta que intente
escapar del `root` del Workspace (§9.3).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .exceptions import WorkspacePathEscapeError

# Estructura fija de WORKSPACE.md §5. Ningún componente crea subcarpetas
# de primer nivel fuera de esta lista.
SUBDIRECTORIOS: tuple[str, ...] = ("artifacts", "logs", "temp", "metadata", "state", "cache")


class WorkspaceLayout:
    def __init__(self, root: str | Path, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self.root = Path(root) / workspace_id

    def materializar(self) -> None:
        """Crea `workspace/<workspace_id>/` y sus seis subcarpetas fijas.
        Idempotente: puede llamarse sobre una estructura ya existente."""
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRECTORIOS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def existe(self) -> bool:
        """WORKSPACE.md §8.2, paso 1: verifica que la estructura existe e
        íntegra antes de intentar recuperar nada."""
        if not self.root.is_dir():
            return False
        return all((self.root / sub).is_dir() for sub in SUBDIRECTORIOS)

    def resolver(self, subdir: str, *partes: str) -> Path:
        """Resuelve una ruta dentro de una subcarpeta fija, rechazando
        cualquier intento de escapar del `root` del Workspace (§9.3, p. ej.
        `../../etc`) antes de tocar disco."""
        if subdir not in SUBDIRECTORIOS:
            raise WorkspacePathEscapeError(f"'{subdir}' no es una subcarpeta válida de Workspace (WORKSPACE.md §5).")
        base = (self.root / subdir).resolve()
        candidata = base.joinpath(*partes).resolve() if partes else base
        try:
            candidata.relative_to(base)
        except ValueError:
            raise WorkspacePathEscapeError(
                f"La ruta {partes!r} intenta escapar de '{subdir}/' (WORKSPACE.md §9.3)."
            ) from None
        return candidata

    def eliminar(self) -> None:
        """Elimina el `root` del Workspace y todo su contenido. La
        prohibición de eliminar un Workspace no terminal (§9.4) es
        responsabilidad de `WorkspaceManager`, no de este componente."""
        shutil.rmtree(self.root, ignore_errors=True)
