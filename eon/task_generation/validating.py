"""
eon.task_generation.validating
===============================
Fase 15 — ValidatingTaskGenerator.

Compone un generador (típicamente `LLMTaskGenerator`) con un
`TaskSpecValidator`. El generador propone; el validador gobierna.

Si la validación falla, la excepción `TaskSpecValidationError` se
propaga al Coordinator, que la traduce en `DelegationError` —
el flujo de fallo estándar del Kernel.
"""

from __future__ import annotations

from typing import Any

from .generator import TaskGenerator
from .validator import TaskSpecValidator


class ValidatingTaskGenerator(TaskGenerator):
    """Compone generador + validador en un solo `TaskGenerator`."""

    def __init__(
        self,
        inner: TaskGenerator,
        validator: TaskSpecValidator,
    ) -> None:
        self._inner = inner
        self._validator = validator

    def generar(self, objetivo: Any) -> list:
        specs = self._inner.generar(objetivo)
        return self._validator.validar(specs)
