"""
eon.workspace.snapshot
=========================
`WorkspaceSnapshot` (WORKSPACE.md §4.5): representación inmutable y
serializable del estado completo de un Workspace en un instante dado --
mismo propósito que `SchedulerSnapshot` (SCHEDULER.md §5.7) y
`VerifierSnapshot` (VERIFIER.md §9.4).

`state/` conserva el snapshot más reciente (§8.2). El snapshot es una
ayuda de auditoría/depuración y un punto de partida rápido para la
reconstrucción en memoria -- nunca la fuente autorizada de `estado`, que
siempre es el `historial` persistido en el `WorkspaceStore` (§8.2, §10.13).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .layout import WorkspaceLayout
from .models import Workspace

_ARCHIVO_SNAPSHOT = "snapshot.json"


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class WorkspaceSnapshot:
    workspace_id: str
    objective_id: str
    estado: str
    configuracion: dict
    metadata: dict
    creado_en: str
    actualizado_en: str
    historial: tuple[dict, ...]
    tomado_en: str = field(default_factory=_ahora)

    @classmethod
    def desde_workspace(cls, ws: Workspace) -> WorkspaceSnapshot:
        return cls(
            workspace_id=ws.id,
            objective_id=ws.objective_id,
            estado=ws.estado.value,
            configuracion=dict(ws.configuracion),
            metadata=dict(ws.metadata),
            creado_en=ws.creado_en,
            actualizado_en=ws.actualizado_en,
            historial=tuple(dict(h) for h in ws.historial),
        )

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "objective_id": self.objective_id,
            "estado": self.estado,
            "configuracion": dict(self.configuracion),
            "metadata": dict(self.metadata),
            "creado_en": self.creado_en,
            "actualizado_en": self.actualizado_en,
            "historial": [dict(h) for h in self.historial],
            "tomado_en": self.tomado_en,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorkspaceSnapshot:
        return cls(
            workspace_id=d["workspace_id"],
            objective_id=d["objective_id"],
            estado=d["estado"],
            configuracion=dict(d.get("configuracion", {})),
            metadata=dict(d.get("metadata", {})),
            creado_en=d["creado_en"],
            actualizado_en=d["actualizado_en"],
            historial=tuple(dict(h) for h in d.get("historial", [])),
            tomado_en=d.get("tomado_en", _ahora()),
        )


def guardar_snapshot(layout: WorkspaceLayout, snapshot: WorkspaceSnapshot) -> None:
    """Persiste el snapshot más reciente en `state/snapshot.json`.
    Escritura atómica (fichero temporal + `replace`) para que una caída a
    mitad de escritura nunca deje un snapshot corrupto (mismo criterio de
    durabilidad que SCHEDULER.md §12.3)."""
    destino = layout.resolver("state", _ARCHIVO_SNAPSHOT)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(destino)


def cargar_snapshot(layout: WorkspaceLayout) -> WorkspaceSnapshot | None:
    """Carga el último snapshot válido desde `state/`, o `None` si aún no
    existe (p. ej. un Workspace recién creado antes de su primera
    transición)."""
    origen = layout.resolver("state", _ARCHIVO_SNAPSHOT)
    if not origen.exists():
        return None
    data = json.loads(origen.read_text(encoding="utf-8"))
    return WorkspaceSnapshot.from_dict(data)
