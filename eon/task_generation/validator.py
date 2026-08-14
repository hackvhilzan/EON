"""
eon.task_generation.validator
==============================
Fase 15 — TaskSpecValidator.

Valida que las TaskSpecs propuestas por un generador (típicamente
`LLMTaskGenerator`) cumplen las reglas de gobierno de EON antes de
que se conviertan en Tasks reales y se ejecuten.

Reglas:
1. Toda `capability_id` debe estar en el conjunto de capabilities permitidas.
2. Toda referencia en `depende_de` debe apuntar a un id existente en el
   mismo lote de TaskSpecs.
3. Cada TaskSpec debe tener un `capability_id` no vacío.

Un LLM puede proponer lo que quiera; EON decide qué se ejecuta.
"""

from __future__ import annotations

from .models import TaskSpec


class TaskSpecValidationError(ValueError):
    """Una TaskSpec propuesta no cumple las reglas de gobierno."""


class TaskSpecValidator:
    """Valida TaskSpecs contra un conjunto de capabilities permitidas."""

    def __init__(self, capabilities_validas: list[str] | None = None) -> None:
        self._capabilities_validas = set(capabilities_validas or [])

    def validar(self, specs: list[TaskSpec]) -> list[TaskSpec]:
        """Valida y devuelve las specs si pasan todas las reglas.
        Lanza `TaskSpecValidationError` si alguna regla se infringe."""
        if not specs:
            return specs

        # Regla 1: capabilities permitidas.
        if self._capabilities_validas:
            for spec in specs:
                if spec.capability_id not in self._capabilities_validas:
                    raise TaskSpecValidationError(
                        f"capability_id '{spec.capability_id}' no está en el conjunto "
                        f"permitido: {sorted(self._capabilities_validas)}"
                    )

        # Regla 2: dependencias deben apuntar a ids existentes.
        ids_existentes = {s.id for s in specs if s.id}
        for spec in specs:
            for dep in spec.depende_de:
                if dep not in ids_existentes:
                    raise TaskSpecValidationError(
                        f"TaskSpec con capability_id='{spec.capability_id}' "
                        f"depende de '{dep}', que no existe en el lote."
                    )

        return specs
