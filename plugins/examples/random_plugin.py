"""
Plugin de ejemplo: Random Number Tool.

Demuestra cómo crear un plugin usando create_tool().
"""
from __future__ import annotations

import random
from typing import Any

from eon.sdk import Tool, ToolResult


class RandomTool(Tool):
    """Tool que genera un número aleatorio."""

    name = "random"

    async def execute(self, min_val: int = 0, max_val: int = 100, **kwargs: Any) -> ToolResult:
        return ToolResult(
            ok=True,
            data={"value": random.randint(min_val, max_val)},
        )


def create_tool() -> Tool:
    """Factory function para crear la tool."""
    return RandomTool()
