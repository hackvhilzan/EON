"""
eon.task_generation.models
===========================
Modelo de `TaskSpec` — la especificación de trabajo generada antes de
que el Planner la convierta en un `eon.planner.task.Task` real.

Mismos cuatro campos que `Task` (`eon.planner.task`), porque describe
lo mismo desde un punto anterior del flujo (Fase 2, Task Generation).
El Coordinator y el PlannerPort reciben `TaskSpec[]`, no `Task[]`; la
conversión la hace el adaptador en `runtime.py` (`_task_spec_a_task`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TaskSpec:
    """Especificación inmutable de una Task, previa a su conversión en
    `eon.planner.task.Task` por el adaptador del Planner.

    Campos idénticos a `Task` (capability_id, id, depende_de, parametros)
    para que la conversión sea trivial y sin pérdida.
    """

    capability_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    depende_de: tuple[str, ...] = field(default_factory=tuple)
    parametros: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id or not self.capability_id.strip():
            raise ValueError("Un TaskSpec necesita `capability_id`.")
        if self.id in self.depende_de:
            raise ValueError(f"Un TaskSpec no puede depender de sí mismo ('{self.id}').")
