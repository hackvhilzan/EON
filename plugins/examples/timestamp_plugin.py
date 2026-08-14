"""
Plugin de ejemplo: Timestamp Tool.

Demuestra cómo crear un plugin usando create_plugin().
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from eon.sdk import Tool, ToolResult, ToolPlugin


class TimestampTool(Tool):
    """Tool que devuelve el timestamp actual."""

    name = "timestamp"

    async def execute(self, **kwargs: Any) -> ToolResult:
        now = datetime.now(UTC)
        return ToolResult(
            ok=True,
            data={
                "iso": now.isoformat(),
                "unix": now.timestamp(),
            },
        )


def create_plugin() -> ToolPlugin:
    """Factory function para crear el plugin."""
    return ToolPlugin(
        name="timestamp_plugin",
        tool=TimestampTool(),
        capability_id="tool.timestamp",
        description="Devuelve el timestamp UTC actual",
    )
