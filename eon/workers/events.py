"""Catálogo de eventos publicados por Workers (Fase 10).

El Worker reporta el resultado de ejecutar una Task publicando exactamente
uno de estos eventos en el EventBus. Nunca produce transiciones silenciosas.
"""

from __future__ import annotations

TASK_COMPLETADA = "task_completada"
TASK_FALLIDA = "task_fallida"

TODOS = frozenset({TASK_COMPLETADA, TASK_FALLIDA})
