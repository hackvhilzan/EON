"""
eon.planner.task
===================
Modelo de Task tal como vive DENTRO de un Plan (Fase 8). PLANNER.md fija el
contrato de Task solo por lo que dice sobre ella desde el punto de vista del
Plan (§1, §4, Invariantes 5-8):

- referencia exactamente una Capability (nunca una Tool, nunca un proveedor
  LLM, nunca infraestructura -- §4, Invariantes 6-7);
- pertenece a un único Plan (Invariante 5) -- por construcción: una Task solo
  existe dentro de `Plan.tasks`, nunca suelta ni compartida entre Planes;
- las Tasks de un Plan poseen un orden total (Invariante 8).

PLANNER.md no define más estructura interna de Task que esa -- el resto
(parámetros de entrada, dependencias entre Tasks del mismo Plan) es detalle de
implementación necesario para poder construir el orden total, igual que
OBJECTIVES.md no fijó los campos de Plan y hubo que decidirlos en PLANNER.md.
Task, a diferencia de Plan y Objective, no tiene máquina de estados propia ni
`historial`: no es una entidad de dominio con ciclo de vida -- es un elemento
inmutable de una lista dentro de un Plan igualmente inmutable en su contenido
(PLANNER.md Invariante 9-10: un Plan nunca almacena resultados ni ejecuta
nada, así que sus Tasks tampoco).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from .planner_exceptions import InvalidPlanError


@dataclass(frozen=True)
class Task:
    """Inmutable de punta a punta -- no tiene excepción de mutabilidad como
    Plan u Objective, porque no tiene estado ni historial propios (ver
    docstring del módulo)."""

    capability_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    depende_de: tuple[str, ...] = field(default_factory=tuple)
    parametros: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id or not self.capability_id.strip():
            raise InvalidPlanError("Una Task necesita `capability_id` (PLANNER.md §4).")
        if self.id in self.depende_de:
            raise InvalidPlanError(f"Una Task no puede depender de sí misma ('{self.id}').")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["depende_de"] = list(self.depende_de)
        return d
