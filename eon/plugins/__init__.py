"""
eon.plugins
==============
Sistema de plugins para registrar Tools custom sin tocar el core.

Un ToolPlugin empaqueta una Tool con su metadatos de gobernanza:
- capability_id: el ID bajo el que se registra en CapabilityMap
- default_policy: ALLOW, DENY o REQUIRES_APPROVAL
- sandbox_profile: límites de ejecución

PluginLoader descubre plugins desde:
- Directorio (EON_PLUGINS_DIR)
- Entry points (pip install eon-plugin-foo)
"""
from __future__ import annotations

from .base import ToolPlugin
from .loader import PluginLoader

__all__ = [
    "ToolPlugin",
    "PluginLoader",
]
