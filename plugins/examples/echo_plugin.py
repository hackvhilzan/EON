"""
Plugin de ejemplo: Echo Tool.

Demuestra cómo crear un plugin simple que registra una Tool custom.
"""
from __future__ import annotations

from typing import Any

from eon.sdk import Tool, ToolResult, ToolPlugin


class EchoTool(Tool):
    """Tool que devuelve el input tal cual."""

    name = "echo"

    async def execute(self, message: str = "", **kwargs: Any) -> ToolResult:
        return ToolResult(
            ok=True,
            data={"echo": message},
        )


# Variable PLUGIN a nivel de módulo (PluginLoader la busca)
PLUGIN = ToolPlugin(
    name="echo_plugin",
    tool=EchoTool(),
    capability_id="tool.echo",
    description="Eco simple: devuelve el mensaje de entrada",
)
