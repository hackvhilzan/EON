"""
eon.tools
==========
Tools — capacidades concretas de acción que el Kernel puede invocar.

Las Tools son async (`async def execute(**kwargs) -> ToolResult`) y se
registran en un `ToolRegistry`. La capa `eon.capabilities.CapabilityExecutor`
traduce el contrato síncrono `bool`-only del Kernel con la interfaz async
de las Tools.

Paquete aislado: no importa `eon.workers`, `eon.scheduler`, `eon.planner`,
`eon.coordinator` ni `eon.objectives`. La única conexión con el Kernel
es a través de `eon.capabilities`.
"""

from __future__ import annotations

from .base_tool import Tool, ToolResult
from .filesystem_tool import FilesystemTool
from .llm_tool import LLMTool
from .registry import ToolRegistry

__all__ = ["Tool", "ToolResult", "ToolRegistry", "FilesystemTool", "LLMTool"]
