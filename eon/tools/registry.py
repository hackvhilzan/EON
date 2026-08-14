"""
eon.tools.registry
====================
ToolRegistry — registra Tools y las ejecuta por nombre.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.registry")


class ToolNotFoundError(Exception):
    """No hay una Tool registrada con ese nombre."""


class ToolRegistry:
    """Registro de Tools por nombre.

    `execute(tool_name, **kwargs)` es async y devuelve un `ToolResult`
    enriquecido. `autodiscover()` registra las Tools por defecto.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Registra una Tool por su `name`."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"No hay Tool registrada como '{name}'.")
        return tool

    def list(self) -> list[str]:
        return sorted(self._tools.keys())

    def close_all(self) -> None:
        """Cierra todas las Tools que tengan método close().

        Maneja tanto close() síncrono como async (ej. BrowserTool).
        """
        import inspect

        for tool in self._tools.values():
            close = getattr(tool, "close", None)
            if not callable(close):
                continue
            try:
                result = close()
                # Si close() es async, obtener la coroutine y cerrarla
                if inspect.iscoroutine(result):
                    result.close()  # cerrar la coroutine sin ejecutarla
            except Exception:
                pass

    async def execute(self, tool_name: str, **kwargs: Any) -> ToolResult:
        """Ejecuta la Tool `tool_name` con los kwargs indicados."""
        tool = self.get(tool_name)
        return await tool.execute(**kwargs)

    def autodiscover(self) -> ToolRegistry:
        """Registra las Tools por defecto del paquete.

        Registra solo las Tools cuyas dependencias están disponibles.
        Las Tools con dependencias opcionales ausentes se omiten
        silenciosamente (no rompen el registro).
        """
        # FilesystemTool — sin dependencias externas
        from .filesystem_tool import FilesystemTool

        self.register(FilesystemTool())

        # Tools con dependencias opcionales
        self._try_register("internet", ".internet_tool", "InternetTool")
        self._try_register("api", ".api_tool", "APITool")
        self._try_register("database", ".database_tool", "DatabaseTool")
        self._try_register("terminal", ".terminal_tool", "TerminalTool")
        self._try_register("python", ".python_tool", "PythonTool")
        self._try_register("pdf", ".pdf_tool", "PDFTool")
        self._try_register("image", ".image_tool", "ImageTool")
        self._try_register("email", ".email_tool", "EmailTool")
        self._try_register("calendar", ".calendar_tool", "CalendarTool")
        self._try_register("browser", ".browser_tool", "BrowserTool")

        return self

    def _try_register(self, tool_name: str, module_path: str, class_name: str) -> None:
        """Intenta registrar una Tool opcional. Falla silenciosamente
        si la dependencia no está disponible."""
        try:
            mod = importlib.import_module(module_path, package=__package__)
            tool_class = getattr(mod, class_name)
            self.register(tool_class())
        except ImportError:
            logger.debug("Tool %s no registrada (dependencia ausente)", tool_name)
        except Exception:
            logger.debug("Tool %s no registrada", tool_name, exc_info=True)

    def discover(self, plugins_dir: str | None = None) -> ToolRegistry:
        """Descubre y registra plugins desde un directorio y entry points.

        Usa PluginLoader para encontrar ToolPlugins en:
        - Directorio (EON_PLUGINS_DIR o plugins_dir explícito)
        - Entry points del grupo "eon.plugins"

        Las Tools de los plugins se registran en este registry.
        """
        from ..plugins import PluginLoader

        loader = PluginLoader(self)
        loader.discover(plugins_dir=plugins_dir)
        return self
