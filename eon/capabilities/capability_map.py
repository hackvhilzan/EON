"""
eon.capabilities.capability_map
==================================
Tabla declarativa que traduce un `capability_id` (string de dominio, decidido
libremente por Planner/TaskGeneration -- ver `eon.planner.task.Task.capability_id`
y `eon.task_generation`) en qué Tool concreta de `eon.tools` hay que invocar y
cómo traducir `parametros` (dict libre de la Task) a los kwargs que esa Tool
espera en `Tool.execute(**kwargs)`.

Vive en `eon.capabilities`, no en `eon.tools` ni en `eon.workers`: ninguno de
los dos debe conocer al otro (MAPA_ARQUITECTURA_KERNEL.md §3.D "Workers -> ...
Tools: No legítimo" / §3.E "Tools -> Coordinator, Planner, LLM para tomar
decisiones de control: No legítimo"). Esta tabla es la única pieza del árbol
que conoce ambos vocabularios a la vez, igual que los adapters de
`eon/runtime.py` conocen ambos lados de un contrato sin que ninguno de los
dos dominios se conozca entre sí.

Convención de capability_id: `"tool.<nombre_de_tool>"` (p. ej.
`"tool.filesystem"`, `"tool.internet"`). `Task.parametros` se pasa tal cual
como kwargs de la Tool salvo que la entrada del mapa declare un
`parametros_mapper` explícito, para el día en que un capability_id de dominio
(p. ej. `"buscar_precio_btc"`) tenga nombres de parámetros distintos a los que
espera la Tool subyacente.

Deliberadamente NO incluye `"tool.terminal"` ni `"tool.python"`: son las dos
Tools de mayor riesgo del set (shell arbitrario / exec arbitrario) y hoy no
hay ningún Verifier real gobernando qué capability se aprueba completar
(`KERNEL_CONSOLIDATION_REPORT.md` §5, "Verifier real desconectado del
runtime"). Ver `eon/CAPABILITIES.md` §3 para la decisión completa. Habilitarlas
es una decisión explícita de quien construya el `CapabilityExecutor`, pasando
un `capability_map` propio -- no el valor por defecto de este módulo.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

ParametrosMapper = Callable[[dict], dict]


def _identidad(parametros: dict) -> dict:
    """Mapeo por defecto: los `parametros` de la Task se pasan tal cual como
    kwargs de `Tool.execute`."""
    return dict(parametros)


@dataclass(frozen=True)
class CapabilitySpec:
    tool_name: str
    parametros_mapper: ParametrosMapper = field(default=_identidad)


# Capabilities habilitadas por defecto -- una por cada Tool de bajo/medio
# riesgo de `eon.tools`. `email`/`calendar`/`api`/`internet`/`llm` requieren
# su propia configuración externa (credenciales SMTP, Google Calendar,
# API key de proveedor de LLM, etc.); si no está configurada, la Tool falla
# en tiempo de ejecución con un error claro (ver cada `*_tool.py`), no en
# tiempo de importación de este mapa. `memory` se sumó tras el rediseño de
# `eon/tools/memory_tool.py` (ya no depende de `eon.core`, ver ese módulo).
CAPABILITY_MAP: dict[str, CapabilitySpec] = {
    "tool.api": CapabilitySpec(tool_name="api"),
    "tool.calendar": CapabilitySpec(tool_name="calendar"),
    "tool.email": CapabilitySpec(tool_name="email"),
    "tool.filesystem": CapabilitySpec(tool_name="filesystem"),
    "tool.internet": CapabilitySpec(tool_name="internet"),
    "tool.llm": CapabilitySpec(tool_name="llm"),
    "tool.memory": CapabilitySpec(tool_name="memory"),
}


class CapabilityNotMappedError(KeyError):
    """`capability_id` no está en el mapa (o fue excluido a propósito, p. ej.
    `tool.terminal`/`tool.python` -- ver el docstring del módulo)."""


def resolver(capability_id: str, capability_map: Mapping[str, CapabilitySpec] | None = None) -> CapabilitySpec:
    """Resuelve un `capability_id` a su `CapabilitySpec`. Lanza
    `CapabilityNotMappedError` si no está en el mapa -- nunca devuelve un
    valor por defecto silencioso (mismo criterio de "sin ejecuciones
    silenciosas" que WORKERS.md Invariante 9)."""
    mapa = capability_map if capability_map is not None else CAPABILITY_MAP
    try:
        return mapa[capability_id]
    except KeyError as exc:
        raise CapabilityNotMappedError(capability_id) from exc


def register_capability(
    capability_id: str,
    tool_name: str,
    parametros_mapper: ParametrosMapper | None = None,
    capability_map: dict[str, CapabilitySpec] | None = None,
) -> None:
    """Registra una capability nueva en el mapa (muta el mapa in-place).

    Permite añadir capabilities dinámicamente desde plugins sin tocar
    el código del core. Si la capability ya existe, se sobrescribe.

    Args:
        capability_id: ID de la capability (ej. "tool.my_custom_tool").
        tool_name: Nombre de la Tool en el ToolRegistry.
        parametros_mapper: Mapper opcional de parámetros.
        capability_map: Mapa a mutar (default: CAPABILITY_MAP global).
    """
    mapa = capability_map if capability_map is not None else CAPABILITY_MAP
    mapper = parametros_mapper if parametros_mapper is not None else _identidad
    mapa[capability_id] = CapabilitySpec(
        tool_name=tool_name,
        parametros_mapper=mapper,
    )


def register_plugin(plugin: Any, capability_map: dict[str, CapabilitySpec] | None = None) -> None:
    """Registra un ToolPlugin en el capability_map.

    Extrae capability_id y tool_name del plugin y los registra.

    Args:
        plugin: Instancia de ToolPlugin (eon.plugins.base.ToolPlugin).
        capability_map: Mapa a mutar (default: CAPABILITY_MAP global).
    """
    register_capability(
        capability_id=plugin.capability_id,
        tool_name=plugin.tool.name,
        capability_map=capability_map,
    )
