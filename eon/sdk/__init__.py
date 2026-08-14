"""
eon.sdk
=========
SDK mínimo para crear Tools custom para EON sin instalar EON completo.

Uso:
    from eon.sdk import Tool, ToolResult, SandboxProfile, ToolPlugin

    class MyTool(Tool):
        name = "my_tool"
        async def execute(self, query: str) -> ToolResult:
            ...
"""
from __future__ import annotations

# Re-export de las interfaces que un autor de plugin necesita
from ..governance.models import PolicyDecision, SandboxProfile
from ..plugins.base import ToolPlugin
from ..tools.base_tool import Tool, ToolResult

__all__ = [
    "Tool",
    "ToolResult",
    "SandboxProfile",
    "PolicyDecision",
    "ToolPlugin",
]
