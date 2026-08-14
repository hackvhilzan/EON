"""
eon.tools.filesystem_tool
===========================
FilesystemTool — lectura y escritura de archivos.

Soporta dos acciones:
- `read`: lee el contenido de un archivo (`path` → `data` con el texto).
- `write`: escribe contenido en un archivo (`path`, `content` → crea o sobrescribe).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .base_tool import Tool, ToolResult


class FilesystemTool(Tool):
    """Tool para operaciones de filesystem (lectura/escritura)."""

    name = "filesystem"

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.monotonic()
        action = kwargs.get("action", "read")
        path_str = kwargs.get("path")

        if not path_str:
            return ToolResult(
                ok=False,
                error="Falta el parámetro 'path'.",
                duration_seconds=time.monotonic() - start,
            )

        path = Path(path_str)

        try:
            if action == "read":
                if not path.exists():
                    return ToolResult(
                        ok=False,
                        error=f"El archivo no existe: {path}",
                        duration_seconds=time.monotonic() - start,
                    )
                data = path.read_text(encoding="utf-8")
                return ToolResult(
                    ok=True,
                    data=data,
                    duration_seconds=time.monotonic() - start,
                )

            if action == "write":
                content = kwargs.get("content", "")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(content), encoding="utf-8")
                return ToolResult(
                    ok=True,
                    data={"path": str(path), "bytes_written": len(str(content))},
                    duration_seconds=time.monotonic() - start,
                )

            return ToolResult(
                ok=False,
                error=f"Acción no soportada: '{action}'. Usa 'read' o 'write'.",
                duration_seconds=time.monotonic() - start,
            )

        except Exception as exc:
            return ToolResult(
                ok=False,
                error=str(exc),
                duration_seconds=time.monotonic() - start,
            )
