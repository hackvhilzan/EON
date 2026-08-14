"""
eon.workspace.artifacts
==========================
`ArtifactRegistry` aplica WORKSPACE.md §5.1 (propiedad de artefactos) sobre
las escrituras y borrados en `artifacts/`. No decide nada de dominio: solo
recuerda qué Worker generó cada ruta relativa y rechaza que otro la
modifique o elimine sin autorización explícita.

Toda ruta se resuelve exclusivamente a través de `WorkspaceLayout`
(§4.4) -- este componente nunca construye rutas a mano.
"""

from __future__ import annotations

from .exceptions import ArtifactOwnershipError
from .layout import WorkspaceLayout


class ArtifactRegistry:
    def __init__(self, layout: WorkspaceLayout, propietarios: dict[str, str] | None = None) -> None:
        self._layout = layout
        self._propietarios: dict[str, str] = dict(propietarios or {})

    @property
    def propietarios(self) -> dict[str, str]:
        return dict(self._propietarios)

    def _verificar_propiedad(self, worker_id: str, ruta_relativa: str, autorizado: bool) -> None:
        propietario_actual = self._propietarios.get(ruta_relativa)
        if propietario_actual is not None and propietario_actual != worker_id and not autorizado:
            raise ArtifactOwnershipError(ruta_relativa, propietario_actual, worker_id)

    def escribir(self, worker_id: str, ruta_relativa: str, contenido: bytes, autorizado: bool = False):
        """Escribe (crea o sobrescribe) un artefacto bajo `artifacts/`. Si
        la ruta ya tiene un propietario distinto de `worker_id`, se rechaza
        salvo que `autorizado=True` -- refleja que la Task que tiene
        asignada el Worker declara esa entrada explícitamente (§5.1). El
        primer Worker que escribe una ruta queda registrado como su
        propietario; una sobreescritura autorizada no cambia esa autoría."""
        self._verificar_propiedad(worker_id, ruta_relativa, autorizado)
        destino = self._layout.resolver("artifacts", ruta_relativa)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
        self._propietarios.setdefault(ruta_relativa, worker_id)
        return destino

    def leer(self, ruta_relativa: str) -> bytes:
        """La lectura no está restringida (§5.1): cualquier Worker puede
        leer libremente cualquier artefacto del Workspace."""
        origen = self._layout.resolver("artifacts", ruta_relativa)
        if not origen.exists():
            raise FileNotFoundError(f"No existe el artefacto {ruta_relativa!r}.")
        return origen.read_bytes()

    def eliminar(self, worker_id: str, ruta_relativa: str, autorizado: bool = False) -> None:
        self._verificar_propiedad(worker_id, ruta_relativa, autorizado)
        destino = self._layout.resolver("artifacts", ruta_relativa)
        if destino.exists():
            destino.unlink()
        self._propietarios.pop(ruta_relativa, None)

    def listar(self) -> list[str]:
        return sorted(self._propietarios.keys())
