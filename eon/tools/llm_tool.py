"""
eon.tools.llm_tool
===================
LLMTool — expone un proveedor LLM como una Tool invocable desde el
`CapabilityExecutor` vía `ToolRegistry`.

El flujo es: Task(capability_id="tool.llm") → CapabilityExecutor →
ToolRegistry.execute("llm", prompt=...) → LLMTool.execute() →
LLM.generate(prompt) → ToolResult(data=respuesta).

Si el LLM lanza, la excepción se captura y se traduce a
`ToolResult(ok=False, error=str(exc))` — nunca escapa hacia el
`CapabilityExecutor` (WORKERS.md Invariante 9).
"""

from __future__ import annotations

from typing import Any

from ..llm.base import LLM
from ..llm.provider import get_provider_lazy
from .base_tool import Tool, ToolResult


class LLMTool(Tool):
    """Tool que delega en un proveedor LLM.

    Si no se inyecta un `llm`, se obtiene uno perezoso vía
    `get_provider_lazy()` — no exige API key/SDK hasta el primer
    `generate()` real.
    """

    name = "llm"

    def __init__(self, llm: LLM | None = None) -> None:
        self._llm = llm or get_provider_lazy()

    async def execute(self, **kwargs: Any) -> ToolResult:
        prompt = kwargs.get("prompt", "")
        try:
            respuesta = self._llm.generate(prompt)
            return ToolResult(ok=True, data=respuesta)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=str(exc))
