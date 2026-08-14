"""
eon.task_generation.generator
===============================
Implementaciones de `TaskGenerationPort` (`eon.coordinator.ports`).

`DeterministicTaskGenerator` es el generador por defecto del runtime:
produce una única Task `default` cuya `capability_id` coincide con la
que el runtime registra como Worker. Es suficiente para el camino feliz
del golden path: un objetivo → una task → un worker → una tool.

Cuando se active la Fase 15 (Autonomous Task Generation), un
`LLMTaskGenerator` validado por `TaskSpecValidator` puede sustituir a
este generador sin que el Coordinator ni el runtime cambien.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from .models import TaskSpec


class TaskGenerator(Protocol):
    """Interfaz que debe satisfacer todo generador de TaskSpecs.

    `DeterministicTaskGenerator` es la implementación por defecto.
    `LLMTaskGenerator` (Fase 15) es la implementación con LLM.
    `ValidatingTaskGenerator` (Fase 15) compone generador + validador.
    """

    def generar(self, objetivo: Any) -> list[TaskSpec]: ...


class DeterministicTaskGenerator:
    """Genera una única TaskSpec `default` desde cualquier objetivo.

    No razona sobre el objetivo: su propósito es proveer una
    especificación mínima y determinista que permita ejecutar el ciclo
    completo del Kernel sin depender de un LLM para la planificación.
    """

    def generar(self, objective: Any) -> list[TaskSpec]:
        """Devuelve una lista con un único `TaskSpec` `default`.

        `objective` debe satisfacer `ObjectiveDescriptorRef` (al menos
        `id`, `descripcion`, `criterio_de_exito`, `estado`), pero este
        generador no inspecciona ninguno de esos campos — solo usa el
        `id` para correlación en logs.
        """
        task_id = str(uuid.uuid4())
        return [
            TaskSpec(
                capability_id="default",
                id=task_id,
                depende_de=(),
                parametros={
                    "objective_id": getattr(objective, "id", None),
                    "descripcion": getattr(objective, "descripcion", ""),
                },
            )
        ]
