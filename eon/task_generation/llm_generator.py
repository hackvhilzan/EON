"""
eon.task_generation.llm_generator
===================================
Fase 15 — LLMTaskGenerator.

Un generador de TaskSpecs que usa un LLM para proponer el plan de
ejecución de un Objetivo. El LLM recibe un prompt con la descripción
del objetivo y las capabilities permitidas, y devuelve un JSON con
la lista de TaskSpecs propuestas.

Regla de Fase 15: "El LLM propone. EON valida, gobierna y ejecuta."
Este generador SOLO propone — la validación la hace `TaskSpecValidator`
(o `ValidatingTaskGenerator` que los compone).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..llm.base import LLM
from .generator import TaskGenerator
from .models import TaskSpec

logger = logging.getLogger("eon.task_generation")


class LLMTaskGenerator(TaskGenerator):
    """Genera TaskSpecs preguntando a un LLM qué Tasks conviene ejecutar.

    El LLM debe devolver un JSON array donde cada elemento tiene los
    campos de `TaskSpec`: `capability_id` (obligatorio), `id` (opcional,
    se autogenera si falta), `depende_de` (opcional, lista de ids),
    `parametros` (opcional, dict).
    """

    def __init__(
        self,
        llm: LLM,
        capabilities_validas: list[str] | None = None,
    ) -> None:
        self._llm = llm
        self._capabilities_validas = capabilities_validas or []

    def generar(self, objetivo: Any) -> list[TaskSpec]:
        prompt = self._construir_prompt(objetivo)
        respuesta = self._llm.generate(prompt)
        return self._parsear_respuesta(respuesta, objetivo)

    def _construir_prompt(self, objetivo: Any) -> str:
        caps = ", ".join(self._capabilities_validas) if self._capabilities_validas else "(sin restricción)"
        descripcion = getattr(objetivo, "descripcion", str(objetivo))
        criterio = getattr(objetivo, "criterio_de_exito", "")
        return (
            f"Objetivo: {descripcion}\n"
            f"Criterio de éxito: {criterio}\n"
            f"Capabilities disponibles: {caps}\n"
            f"Responde con un JSON array de tareas. Cada tarea debe tener:\n"
            f'- "capability_id": una de las capabilities disponibles\n'
            f'- "id": identificador único (opcional)\n'
            f'- "depende_de": lista de ids de tareas precedentes (opcional)\n'
            f'- "parametros": diccionario de parámetros (opcional)\n'
        )

    def _parsear_respuesta(self, respuesta: str, objetivo: Any) -> list[TaskSpec]:
        try:
            data = json.loads(respuesta)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"La respuesta del LLM no es JSON válido: {exc}") from exc

        if not isinstance(data, list):
            raise ValueError(f"La respuesta del LLM debe ser un JSON array, no {type(data).__name__}")

        objective_id = getattr(objetivo, "id", None)
        specs: list[TaskSpec] = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError(f"Cada tarea debe ser un objeto JSON, no {type(item).__name__}")
            capability_id = item.get("capability_id")
            if not capability_id:
                raise ValueError("Cada tarea debe tener 'capability_id'.")

            task_id = item.get("id")
            depende_de = item.get("depende_de", [])
            parametros = item.get("parametros", {})

            if objective_id and "objective_id" not in parametros:
                parametros = {**parametros, "objective_id": objective_id}

            specs.append(
                TaskSpec(
                    capability_id=capability_id,
                    id=task_id,
                    depende_de=depende_de,
                    parametros=parametros,
                )
            )

        return specs
