"""
eon.coordinator.models
========================
`CoordinatorExecution` (ORDEN MAESTRA, "ESTADO INTERNO"): la entidad de
dominio del Coordinator. Mantiene únicamente `execution_id`, `objective_id`,
`plan_id`, `workspace_id`, `package_id`, `estado`, timestamps e `historial`
-- "Nada más", tal como exige el contrato. El Coordinator no posee
`configuracion` ni `metadata` propios: esos campos pertenecen al Objetivo y
al Workspace respectivamente, y el Coordinator no los duplica (ORDEN
MAESTRA, "AISLAMIENTO": solo puede invocar interfaces públicas, nunca
absorber datos de dominio ajeno).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class CoordinatorState(str, Enum):
    """Refleja exactamente el ORDEN DE EJECUCIÓN del contrato:

        Objective -> Planner -> Scheduler -> Workers -> Verifier ->
        Workspace -> Package -> Resultado Final

    con una vuelta atrás (`VERIFYING -> REPLANNING -> PLANNING`), reflejo a
    nivel de Coordinator del mismo ciclo que ya modelan PLANNER.md §7
    (replanificación = versión nueva de Plan) y WORKSPACE.md §3
    (`VERIFYING -> PLANNING`). El contrato de la Fase 13 no fija nombres de
    estados -- solo el orden y las responsabilidades -- por lo que estos
    nombres son una decisión de API (ver informe)."""

    CREATED = "created"
    CREATING_OBJECTIVE = "creating_objective"
    PLANNING = "planning"
    SCHEDULING = "scheduling"
    RUNNING = "running"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    AWAITING_WORKSPACE = "awaiting_workspace"
    PACKAGING = "packaging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINALES = frozenset({CoordinatorState.COMPLETED, CoordinatorState.FAILED, CoordinatorState.CANCELLED})
NO_TERMINALES = frozenset(CoordinatorState) - TERMINALES


@dataclass
class CoordinatorExecution:
    """Estado interno de una ejecución orquestada por el Coordinator
    (ORDEN MAESTRA, "ESTADO INTERNO"). Igual que `Plan` (PLANNER.md §1) y
    `Package` (PACKAGE.md §1), es inmutable salvo por los campos que la
    propia máquina de estados necesita mutar: `objective_id`, `plan_id`,
    `workspace_id`, `package_id` se rellenan progresivamente a medida que
    cada delegación produce su identificador (nunca se reescriben una vez
    fijados, salvo `plan_id` en una replanificación -- ver §7 de
    `PLANNER.md`, nueva versión, mismo Objetivo), y `estado`, `historial`,
    `actualizado_en` mutan en cada transición."""

    objective_id: str | None = None
    plan_id: str | None = None
    workspace_id: str | None = None
    package_id: str | None = None
    estado: CoordinatorState = CoordinatorState.CREATED
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    creado_en: str = field(default_factory=_ahora)
    actualizado_en: str = field(default_factory=_ahora)
    historial: list[dict] = field(default_factory=list)

    @property
    def execution_id(self) -> str:
        return self.id

    def es_terminal(self) -> bool:
        return self.estado in TERMINALES

    def registrar_evento(
        self,
        evento: str,
        de: str | None,
        a: str,
        motivo: str | None = None,
    ) -> None:
        """Anota una transición en `historial` de forma append-only
        (ORDEN MAESTRA, "PERSISTENCIA": "Nunca podrá existir una
        transición silenciosa"). No decide la transición -- eso es
        responsabilidad de `validators.py`; este método solo registra lo
        ya decidido."""
        self.actualizado_en = _ahora()
        entrada: dict[str, Any] = {
            "evento": evento,
            "de": de,
            "a": a,
            "cuando": self.actualizado_en,
        }
        if motivo is not None:
            entrada["motivo"] = motivo
        self.historial.append(entrada)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "plan_id": self.plan_id,
            "workspace_id": self.workspace_id,
            "package_id": self.package_id,
            "estado": self.estado.value,
            "creado_en": self.creado_en,
            "actualizado_en": self.actualizado_en,
            "historial": [dict(h) for h in self.historial],
        }

    @classmethod
    def from_dict(cls, d: dict) -> CoordinatorExecution:
        return cls(
            id=d["id"],
            objective_id=d.get("objective_id"),
            plan_id=d.get("plan_id"),
            workspace_id=d.get("workspace_id"),
            package_id=d.get("package_id"),
            estado=CoordinatorState(d["estado"]),
            creado_en=d["creado_en"],
            actualizado_en=d["actualizado_en"],
            historial=[dict(h) for h in d.get("historial", [])],
        )
