"""
eon.workspace.models
=======================
Modelos de dominio de Workspace (Fase 11). Aislado del Kernel -- el
Workspace no pertenece a él (WORKSPACE.md §0, §10.12) y no importa
`eon.objectives`, `eon.planner`, `eon.scheduler`, `eon.workers` ni
`eon.verifier`. Solo conoce identificadores en forma de string.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .exceptions import InvalidWorkspaceError


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class WorkspaceState(str, Enum):
    """WORKSPACE.md §3: CREATED -> PLANNING -> SCHEDULING -> RUNNING ->
    VERIFYING -> COMPLETED, con el ciclo VERIFYING -> PLANNING para
    replanificación, y CANCELLED alcanzable desde cualquier estado no
    terminal."""

    CREATED = "created"
    PLANNING = "planning"
    SCHEDULING = "scheduling"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINALES = frozenset({WorkspaceState.COMPLETED, WorkspaceState.FAILED, WorkspaceState.CANCELLED})
NO_TERMINALES = frozenset(WorkspaceState) - TERMINALES


@dataclass
class Workspace:
    """WORKSPACE.md §1. Mutable únicamente en `estado`, `metadata`,
    `historial` y `actualizado_en` -- mismo criterio de inmutabilidad
    parcial que `Plan` (PLANNER.md §1) y `Worker` (WORKERS.md)."""

    objective_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    estado: WorkspaceState = WorkspaceState.CREATED
    configuracion: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    creado_en: str = field(default_factory=_ahora)
    actualizado_en: str = field(default_factory=_ahora)
    historial: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.objective_id or not str(self.objective_id).strip():
            raise InvalidWorkspaceError(
                "Un Workspace necesita `objective_id` (siempre un Objetivo raíz, WORKSPACE.md §1)."
            )
        if isinstance(self.estado, str):
            self.estado = WorkspaceState(self.estado)

    def es_terminal(self) -> bool:
        return self.estado in TERMINALES

    def registrar_evento(
        self, evento: str, de: str | None, a: str | None, motivo: str | None = None, **extra: Any
    ) -> None:
        entrada = {"evento": evento, "de": de, "a": a, "motivo": motivo, "ts": _ahora(), "extra": extra}
        self.historial.append(entrada)
        self.actualizado_en = entrada["ts"]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "estado": self.estado.value,
            "configuracion": dict(self.configuracion),
            "metadata": dict(self.metadata),
            "creado_en": self.creado_en,
            "actualizado_en": self.actualizado_en,
            "historial": [dict(h) for h in self.historial],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Workspace:
        return cls(
            objective_id=d["objective_id"],
            id=d["id"],
            estado=WorkspaceState(d["estado"]),
            configuracion=dict(d.get("configuracion", {})),
            metadata=dict(d.get("metadata", {})),
            creado_en=d["creado_en"],
            actualizado_en=d["actualizado_en"],
            historial=[dict(h) for h in d.get("historial", [])],
        )
