"""
eon.planner.models
=====================
Modelo único de Plan (Fase 8). Implementa exactamente lo que define
PLANNER.md §1 (contrato congelado v1.0) -- sin añadir campos, sin quitar
campos. Mismo espíritu que eon.objectives.models.Objective: un único
dataclass del que habla todo el resto del sistema, con su propio historial
append-only.

Inmutabilidad (§1): un Plan es inmutable salvo por `estado`, `historial` y
`actualizado_en`. Este módulo no impone esa regla con magia de Python (frozen
dataclasses harían `estado` también inmutable, justo lo contrario de lo que
hace falta) -- la impone PlannerManager, que es el único punto de escritura,
igual que ObjectiveManager lo es para Objective.

El campo `tasks` referencia objetos `Task` (eon.planner.task) -- no se
importa aquí por nombre para evitar un ciclo de imports entre `models.py` y
`task.py`; quien construye un Plan pasa ya la lista de Task construida.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .planner_exceptions import InvalidPlanError


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class PlanState(str, Enum):
    """Los cuatro estados de la máquina de PLANNER.md §8: tres no-terminales
    (creado, activo, obsoleto) y un terminal de verdad (cancelado)."""

    CREADO = "creado"
    ACTIVO = "activo"
    OBSOLETO = "obsoleto"
    CANCELADO = "cancelado"

    @property
    def es_terminal(self) -> bool:
        return self == PlanState.CANCELADO


class PlanOrigin(str, Enum):
    """PLANNER.md §2: los tres momentos en que un Plan puede nacer. No es un
    campo normativo de §1 (PLANNER.md no lo exige explícitamente en la tabla
    de campos mínimos), pero se necesita para decidir si el nacimiento emite
    `plan_creado` o `plan_replanificado` (§9) -- se modela aquí, no se infiere
    por fuera, para que ningún implementador tenga que adivinarlo. Se guarda
    únicamente en el `historial`/`metadata`, nunca como campo de primer nivel
    del Plan (§1 no lo lista)."""

    PLANIFICACION_INICIAL = "planificacion_inicial"
    REPLANIFICACION = "replanificacion"
    REINTENTO = "reintento"


@dataclass
class PlanHistoryEntry:
    """Una entrada del `historial` append-only de un Plan (§1). Cubre las
    transiciones de estado de §8 (de/a presentes) -- no existen, a diferencia
    de Objective, eventos estructurales sin transición: en PLANNER.md todo
    evento de §9 corresponde a una transición o a un nacimiento."""

    evento: str
    ts: str
    de: str | None = None
    a: str | None = None
    motivo: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Plan:
    """Exactamente los campos de PLANNER.md §1. No añadir ni quitar ninguno."""

    objective_id: str
    tasks: list[Any] = field(default_factory=list)  # list[Task], ver docstring del módulo
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: int = 1
    estado: PlanState = PlanState.CREADO
    metadata: dict = field(default_factory=dict)
    creado_en: str = field(default_factory=_ahora)
    actualizado_en: str = field(default_factory=_ahora)
    historial: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        # §1: un Plan describe qué Tasks deben ejecutarse -- una lista vacía
        # no es una estrategia. §4: cada Task referencia exactamente una
        # Capability, validado por quien construye la Task (eon.planner.task),
        # no aquí -- Plan solo exige que la lista no esté vacía.
        if not self.objective_id or not self.objective_id.strip():
            raise InvalidPlanError("Un Plan necesita `objective_id`.")
        if not self.tasks:
            raise InvalidPlanError("Un Plan sin Tasks no es una estrategia válida (PLANNER.md §1).")
        if isinstance(self.estado, str):
            self.estado = PlanState(self.estado)
        if self.version < 1:
            raise InvalidPlanError("`version` debe ser >= 1.")

    # ---- historial (única forma de anotar algo; nunca se edita `historial` a mano) ----

    def registrar_transicion(self, de: str | None, a: str, evento: str, motivo: str | None = None, **extra) -> None:
        entrada = PlanHistoryEntry(evento=evento, ts=_ahora(), de=de, a=a, motivo=motivo, extra=extra)
        self.historial.append(entrada.to_dict())
        self.actualizado_en = entrada.ts

    def to_dict(self) -> dict:
        d = asdict(self)
        d["estado"] = self.estado.value
        d["tasks"] = [t.to_dict() if hasattr(t, "to_dict") else t for t in self.tasks]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Plan:
        from .task import Task

        tasks_data = d.get("tasks", [])
        tasks: list[Task] = []
        for t in tasks_data:
            if isinstance(t, Task):
                tasks.append(t)
            elif isinstance(t, dict):
                tasks.append(
                    Task(
                        capability_id=t["capability_id"],
                        id=t.get("id") or str(__import__("uuid").uuid4()),
                        depende_de=tuple(t.get("depende_de", [])),
                        parametros=dict(t.get("parametros", {})),
                    )
                )
        return cls(
            objective_id=d["objective_id"],
            tasks=tasks,
            id=d.get("id") or str(__import__("uuid").uuid4()),
            version=d.get("version", 1),
            estado=PlanState(d.get("estado", "creado")),
            metadata=dict(d.get("metadata", {})),
            creado_en=d.get("creado_en", _ahora()),
            actualizado_en=d.get("actualizado_en", _ahora()),
            historial=list(d.get("historial", [])),
        )
