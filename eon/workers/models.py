"""
eon.workers.models
====================
Modelo de Worker — la unidad de ejecución que procesa Tasks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum


class WorkerState(str, Enum):
    """Estado de un Worker en un momento dado."""

    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"

    @property
    def es_terminal(self) -> bool:
        return self is WorkerState.OFFLINE


@dataclass
class Worker:
    """Un Worker registrado con sus capabilities y estado actual.

    Un Worker puede procesar Tasks cuya `capability_id` coincida con
    una de sus `capabilities`. Mientras ejecuta una Task, pasa a BUSY;
    al terminar, vuelve a IDLE.
    """

    capabilities: tuple[str, ...]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    nombre: str = ""
    estado: WorkerState = WorkerState.IDLE
    task_actual: str | None = None

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ValueError("Un Worker necesita al menos una capability.")
        if not self.nombre:
            self.nombre = f"worker-{self.id[:8]}"

    def soporta(self, capability_id: str) -> bool:
        return capability_id in self.capabilities

    def declara(self, capability_id: str) -> bool:
        """Alias de `soporta` — comprueba si el Worker declara soporte
        para una capability dada."""
        return capability_id in self.capabilities

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "capabilities": list(self.capabilities),
            "nombre": self.nombre,
            "estado": self.estado.value if hasattr(self.estado, "value") else str(self.estado),
            "task_actual": self.task_actual,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Worker:
        return cls(
            capabilities=tuple(d.get("capabilities", [])),
            id=d.get("id") or str(uuid.uuid4()),
            nombre=d.get("nombre", ""),
            estado=WorkerState(d.get("estado", "idle")),
            task_actual=d.get("task_actual"),
        )
