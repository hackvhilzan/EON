"""
eon.tools.base_tool
=====================
Interfaz base que deben implementar todas las Tools.

Una Tool es una capacidad concreta de acción: leer/escribir archivos,
acceder a internet, llamar a una API, etc. El Kernel invoca las Tools
a través de `eon.capabilities.CapabilityExecutor`, que traduce el
contrato síncrono `bool`-only con la interfaz async enriquecida.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Resultado enriquecido de una ejecución de Tool.

    El Kernel solo ve `ok` (bool) a través del `CapabilityExecutor`;
    los campos `data`, `error` y `duration_seconds` están disponibles
    via `executor.ultimo_resultado` para quien construya/observe el
    executor fuera del Kernel.
    """

    ok: bool
    data: Any = None
    error: str | None = None
    duration_seconds: float = 0.0


class Tool(ABC):
    """Interfaz mínima que deben implementar todas las Tools.

    Cada Tool tiene un `name` único (usado por `CapabilityMap` para
    mapear `capability_id → tool_name`) y un método `execute` async.
    """

    name: str = "base"

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Ejecuta la acción de la Tool con los parámetros indicados."""
        ...
