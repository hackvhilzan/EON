"""
eon.coordinator.snapshot
===========================
`CoordinatorSnapshot`: representación inmutable y serializable del estado
completo de una ejecución en un instante dado -- mismo propósito que
`WorkspaceSnapshot`, `SchedulerSnapshot`, `VerifierSnapshot` y
`PackageSnapshot`. Ayuda de auditoría/depuración y punto de partida rápido
para la reconstrucción en memoria -- nunca la fuente autorizada de
`estado`, que siempre es el `historial` persistido en el
`CoordinatorStore` (mismo criterio que PACKAGE.md §8.2 / WORKSPACE.md §8.2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .layout import CoordinatorLayout
from .models import CoordinatorExecution

_ARCHIVO_SNAPSHOT = "snapshot.json"


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class CoordinatorSnapshot:
    execution_id: str
    objective_id: str | None
    plan_id: str | None
    workspace_id: str | None
    package_id: str | None
    estado: str
    creado_en: str
    actualizado_en: str
    historial: tuple[dict, ...]
    tomado_en: str = field(default_factory=_ahora)

    @classmethod
    def desde_execution(cls, execution: CoordinatorExecution) -> CoordinatorSnapshot:
        return cls(
            execution_id=execution.id,
            objective_id=execution.objective_id,
            plan_id=execution.plan_id,
            workspace_id=execution.workspace_id,
            package_id=execution.package_id,
            estado=execution.estado.value,
            creado_en=execution.creado_en,
            actualizado_en=execution.actualizado_en,
            historial=tuple(dict(h) for h in execution.historial),
        )

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "objective_id": self.objective_id,
            "plan_id": self.plan_id,
            "workspace_id": self.workspace_id,
            "package_id": self.package_id,
            "estado": self.estado,
            "creado_en": self.creado_en,
            "actualizado_en": self.actualizado_en,
            "historial": [dict(h) for h in self.historial],
            "tomado_en": self.tomado_en,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CoordinatorSnapshot:
        return cls(
            execution_id=d["execution_id"],
            objective_id=d.get("objective_id"),
            plan_id=d.get("plan_id"),
            workspace_id=d.get("workspace_id"),
            package_id=d.get("package_id"),
            estado=d["estado"],
            creado_en=d["creado_en"],
            actualizado_en=d["actualizado_en"],
            historial=tuple(dict(h) for h in d.get("historial", [])),
            tomado_en=d.get("tomado_en", _ahora()),
        )


def guardar_snapshot(layout: CoordinatorLayout, snapshot: CoordinatorSnapshot) -> None:
    """Escritura atómica (fichero temporal + `replace`) para que una caída
    a mitad de escritura nunca deje un snapshot corrupto."""
    destino = layout.resolver("state", _ARCHIVO_SNAPSHOT)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(destino)


def cargar_snapshot(layout: CoordinatorLayout) -> CoordinatorSnapshot | None:
    origen = layout.resolver("state", _ARCHIVO_SNAPSHOT)
    if not origen.exists():
        return None
    data = json.loads(origen.read_text(encoding="utf-8"))
    return CoordinatorSnapshot.from_dict(data)
