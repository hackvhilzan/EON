"""
eon.coordinator.layout
=========================
`CoordinatorLayout` es el único componente que construye rutas de archivo
para una ejecución (mismo patrón que `PackageLayout`/`WorkspaceLayout`).
El Coordinator no posee `artifacts/` -- eso es exclusivo del Workspace
(WORKSPACE.md §5) -- ni `content/`/`manifest/` -- eso es exclusivo del
Package (PACKAGE.md §5). Solo necesita persistir su propio estado interno
mínimo (ORDEN MAESTRA, "ESTADO INTERNO") y su traza de auditoría.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .exceptions import CoordinatorPathEscapeError

# Estructura fija. Ningún componente crea subcarpetas de primer nivel
# fuera de esta lista (mismo criterio que PACKAGE.md §5 / WORKSPACE.md §5).
SUBDIRECTORIOS: tuple[str, ...] = ("state", "logs", "temp")


def _resolver_seguro(base: Path, partes: tuple[str, ...], contexto: str) -> Path:
    base = base.resolve()
    candidata = base.joinpath(*partes).resolve() if partes else base
    try:
        candidata.relative_to(base)
    except ValueError:
        raise CoordinatorPathEscapeError(f"La ruta {partes!r} intenta escapar de '{contexto}'.") from None
    return candidata


class CoordinatorLayout:
    def __init__(self, root: str | Path, execution_id: str) -> None:
        self.execution_id = execution_id
        self.root = Path(root) / execution_id

    def materializar(self) -> None:
        """Crea `coordinator/<execution_id>/` y sus subcarpetas fijas.
        Idempotente: puede llamarse sobre una estructura ya existente."""
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRECTORIOS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def existe(self) -> bool:
        if not self.root.is_dir():
            return False
        return all((self.root / sub).is_dir() for sub in SUBDIRECTORIOS)

    def resolver(self, subdir: str, *partes: str) -> Path:
        if subdir not in SUBDIRECTORIOS:
            raise CoordinatorPathEscapeError(f"'{subdir}' no es una subcarpeta válida de Coordinator.")
        return _resolver_seguro(self.root / subdir, partes, f"{subdir}/")

    def eliminar(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
