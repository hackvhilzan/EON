"""
eon.task_generation
====================
Fase 2 — Task Generation. Genera la especificación de trabajo (TaskSpec[])
a partir de un Objetivo, antes de que el Planner la convierta en un Plan.

El Coordinator nunca decide cómo se generan las Tasks: delega en
`TaskGenerationPort.generar(objetivo) -> list[TaskSpec]`. Este paquete
proporciona tres implementaciones:

- `DeterministicTaskGenerator`: genera una única Task `default` desde la
  descripción del objetivo. Es el generador por defecto del runtime.
- `LLMTaskGenerator` (Fase 15): usa un LLM para proponer TaskSpecs.
- `ValidatingTaskGenerator` (Fase 15): compone un generador con un
  `TaskSpecValidator` que gobierna qué propuestas se aceptan.
- `TaskSpec`: el modelo de especificación (capability_id, id, depende_de,
  parametros), idéntico en campos a `eon.planner.task.Task`.
"""

from __future__ import annotations

from .generator import DeterministicTaskGenerator, TaskGenerator
from .governed import GovernedTaskGenerator, GovernedTaskRejectionError
from .llm_generator import LLMTaskGenerator
from .models import TaskSpec
from .validating import ValidatingTaskGenerator
from .validator import TaskSpecValidationError, TaskSpecValidator

__all__ = [
    "TaskSpec",
    "TaskGenerator",
    "DeterministicTaskGenerator",
    "LLMTaskGenerator",
    "TaskSpecValidator",
    "TaskSpecValidationError",
    "ValidatingTaskGenerator",
    "GovernedTaskGenerator",
    "GovernedTaskRejectionError",
]
