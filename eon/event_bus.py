"""
eon.event_bus
================
EventBus síncrono compartido por todos los módulos del Kernel.

Antes duplicado en `eon.objectives.events` (copia local para mantener
Objectives aislado). Este módulo es la versión canónica del Kernel: la
que importan `runtime.py`, `coordinator`, `scheduler`, `planner`,
`workspace`, `workers` y `package`.

Diseño:
- Síncrono: los callbacks se ejecutan en el mismo hilo que `emit`.
- Robusto: un callback que lanza una excepción no rompe el flujo
  (se captura y se ignora silenciosamente, igual que en
  `eon.objectives.events.EventBus`).
- Mínimo: solo `subscribe` (alias `on`) y `emit`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("eon.event_bus")


class EventBus:
    """Bus de eventos síncrono, mínimo, compartido por el Kernel."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_name: str, callback: Callable) -> None:
        """Registra `callback` para ejecutarse cuando se emita `event_name`."""
        self._subscribers[event_name].append(callback)

    # Alias para compatibilidad con `eon.objectives.events.EventBus.on`.
    on = subscribe

    def emit(self, event_name: str, **data: Any) -> None:
        """Emite `event_name` con `data` como kwargs para cada callback.

        Las excepciones de los callbacks se capturan y se registran en
        logs, pero no se propagan — el flujo del Kernel no debe romper
        porque un subscriptor falle.
        """
        for callback in self._subscribers.get(event_name, []):
            try:
                callback(**data)
            except Exception:
                logger.exception("Error en callback del EventBus para evento '%s'", event_name)
