"""
eon.plugins.loader
====================
PluginLoader — descubre y carga plugins desde directorios y entry points.

Mecanismos de discovery:
1. load_directory(path): importa todos los .py de un directorio,
   busca instancias de ToolPlugin (variable `PLUGIN` o función `create_plugin()`).
2. load_entry_points(): usa importlib.metadata para encontrar plugins
   registrados como entry points del grupo "eon.plugins".
3. discover(): combina ambos mecanismos.

Configuración:
    EON_PLUGINS_DIR=/data/plugins

Hot-reload: reload_directory() recarga plugins sin reiniciar el proceso.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from ..tools.base_tool import Tool
from ..tools.registry import ToolRegistry
from .base import ToolPlugin

logger = logging.getLogger("eon.plugins")


class PluginLoader:
    """Carga plugins de Tools desde directorios y entry points.

    Uso:
        loader = PluginLoader(registry=tool_registry)
        plugins = loader.load_directory("/data/plugins")
        # Las Tools están registradas en el registry

        # O con auto-discovery:
        plugins = loader.discover(plugins_dir="/data/plugins")
    """

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry
        self._loaded: list[ToolPlugin] = []
        self._module_paths: dict[str, Path] = {}
        self._plugin_to_module: dict[str, str] = {}  # plugin.name → module_name

    @property
    def loaded_plugins(self) -> list[ToolPlugin]:
        """Lista de plugins cargados."""
        return list(self._loaded)

    def load_directory(self, path: str | Path) -> list[ToolPlugin]:
        """Carga plugins desde un directorio.

        Busca archivos .py y busca:
        - Variable `PLUGIN: ToolPlugin` a nivel de módulo
        - Función `create_plugin() -> ToolPlugin`
        - Función `create_tool() -> Tool` (crea plugin con defaults)

        Args:
            path: Directorio a escanear.

        Returns:
            Lista de plugins cargados.
        """
        dir_path = Path(path)
        if not dir_path.is_dir():
            logger.warning("Directorio de plugins no existe: %s", dir_path)
            return []

        plugins: list[ToolPlugin] = []

        for py_file in sorted(dir_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            plugin = self._load_plugin_file(py_file)
            if plugin is not None:
                module_name = f"eon_plugin_{py_file.stem}"
                self._plugin_to_module[plugin.name] = module_name
                plugins.append(plugin)

        for plugin in plugins:
            self._register_plugin(plugin)

        self._loaded.extend(plugins)
        return plugins

    def load_entry_points(self) -> list[ToolPlugin]:
        """Carga plugins registrados como entry points.

        Busca entry points del grupo "eon.plugins" usando importlib.metadata.
        Cada entry point debe apuntar a una función `create_plugin() -> ToolPlugin`
        o a una instancia de ToolPlugin.

        Returns:
            Lista de plugins cargados.
        """
        plugins: list[ToolPlugin] = []

        try:
            from importlib.metadata import entry_points

            eps = entry_points()
            # Python 3.12+: entry_points() devuelve EntryPoints (selectable)
            if hasattr(eps, "select"):
                eon_eps = eps.select(group="eon.plugins")
            else:
                # Python 3.11: entry_points() returns dict
                eon_eps = eps.get("eon.plugins", [])

            for ep in eon_eps:
                try:
                    obj = ep.load()
                    if callable(obj) and not isinstance(obj, ToolPlugin):
                        plugin = obj()
                    elif isinstance(obj, ToolPlugin):
                        plugin = obj
                    else:
                        logger.warning("Entry point %s no devolvió ToolPlugin", ep.name)
                        continue

                    if isinstance(plugin, ToolPlugin):
                        self._register_plugin(plugin)
                        plugins.append(plugin)
                except Exception:
                    logger.exception("Error cargando entry point %s", ep.name)
        except Exception:
            logger.debug("No se pudieron cargar entry points", exc_info=True)

        self._loaded.extend(plugins)
        return plugins

    def discover(
        self,
        plugins_dir: str | Path | None = None,
        env_var: str = "EON_PLUGINS_DIR",
    ) -> list[ToolPlugin]:
        """Descubre plugins desde directorio y entry points.

        Args:
            plugins_dir: Directorio explícito. Si es None, usa la variable
                de entorno `env_var`.
            env_var: Nombre de la variable de entorno para el directorio.

        Returns:
            Lista de todos los plugins cargados.
        """
        import os

        plugins: list[ToolPlugin] = []

        # Entry points primero
        plugins.extend(self.load_entry_points())

        # Directorio
        if plugins_dir is None:
            plugins_dir = os.environ.get(env_var)

        if plugins_dir:
            plugins.extend(self.load_directory(plugins_dir))

        return plugins

    def reload_directory(self, path: str | Path) -> list[ToolPlugin]:
        """Recarga plugins desde un directorio (hot-reload).

        Elimina los módulos previamente cargados y los recarga.
        También limpia el CAPABILITY_MAP global de capabilities obsoletas.
        """
        dir_path = Path(path)
        # Descargar módulos previos de este directorio
        to_remove = [
            name for name, mod_path in self._module_paths.items()
            if mod_path.parent == dir_path
        ]
        for name in to_remove:
            if name in sys.modules:
                del sys.modules[name]
            self._module_paths.pop(name, None)

        # Remover plugins cuyos módulos fueron descargados y limpiar CAPABILITY_MAP
        plugins_to_remove = [
            p for p in self._loaded
            if self._plugin_to_module.get(p.name, "") in to_remove
        ]
        for p in plugins_to_remove:
            try:
                from ..capabilities.capability_map import CAPABILITY_MAP
                CAPABILITY_MAP.pop(p.capability_id, None)
            except Exception:
                pass
            self._plugin_to_module.pop(p.name, None)
        self._loaded = [
            p for p in self._loaded
            if self._plugin_to_module.get(p.name, "") not in to_remove
        ]

        return self.load_directory(dir_path)

    def _load_plugin_file(self, py_file: Path) -> ToolPlugin | None:
        """Carga un plugin desde un archivo .py."""
        module_name = f"eon_plugin_{py_file.stem}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("No se pudo crear spec para %s", py_file)
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            self._module_paths[module_name] = py_file

            # Buscar PLUGIN (instancia de ToolPlugin)
            plugin = getattr(module, "PLUGIN", None)
            if isinstance(plugin, ToolPlugin):
                return plugin

            # Buscar create_plugin() -> ToolPlugin
            create_plugin = getattr(module, "create_plugin", None)
            if callable(create_plugin):
                result = create_plugin()
                if isinstance(result, ToolPlugin):
                    return result

            # Buscar create_tool() -> Tool (crear plugin con defaults)
            create_tool = getattr(module, "create_tool", None)
            if callable(create_tool):
                tool = create_tool()
                if isinstance(tool, Tool):
                    return ToolPlugin(
                        name=py_file.stem,
                        tool=tool,
                    )

            logger.debug("Archivo %s no define PLUGIN, create_plugin() ni create_tool()", py_file)
            return None

        except Exception:
            logger.exception("Error cargando plugin desde %s", py_file)
            return None

    def _register_plugin(self, plugin: ToolPlugin) -> None:
        """Registra una Tool en el ToolRegistry y en el CapabilityMap."""
        if self._registry is not None:
            try:
                self._registry.register(plugin.tool)
                logger.info(
                    "Plugin %s registrado: tool=%s, capability=%s",
                    plugin.name,
                    plugin.tool.name,
                    plugin.capability_id,
                )
            except Exception:
                logger.exception("Error registrando plugin %s", plugin.name)

        # Registrar capability en el CapabilityMap global
        try:
            from ..capabilities.capability_map import register_plugin as _reg_plugin
            _reg_plugin(plugin)
            logger.debug(
                "Capability %s registrada en CapabilityMap para plugin %s",
                plugin.capability_id,
                plugin.name,
            )
        except Exception:
            logger.debug("No se pudo registrar capability", exc_info=True)
