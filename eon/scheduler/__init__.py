"""
eon.scheduler
=============
Fase 9 de EON. Convierte un Plan activo (Fase 8) en trabajo ejecutable,
decidiendo qué Task puede ejecutarse en cada momento según su grafo de
dependencias. No ejecuta Tasks, no conoce Tools, LLM ni infraestructura.

Paquete autocontenido: no importa `eon.planner` ni `eon.objectives`. Toda
comunicación con el resto del sistema ocurre exclusivamente vía EventBus
(mismo patrón que `eon.objectives` y `eon.planner`).
"""

from __future__ import annotations

from . import dependency_resolver, events
from .exceptions import (
    DependencyCycleError,
    IllegalSchedulerTransitionError,
    InvalidPlanError,
    SchedulerAlreadyExistsError,
    SchedulerError,
    SchedulerNotFoundError,
    TaskNotFoundError,
)
from .models import (
    SchedulerRun,
    SchedulerSnapshot,
    SchedulerState,
    TaskExecutionRecord,
    TaskExecutionState,
)
from .scheduler import SchedulerManager
from .scheduler_store import InMemorySchedulerStore, SchedulerStore

__all__ = [
    "DependencyCycleError",
    "IllegalSchedulerTransitionError",
    "InMemorySchedulerStore",
    "InvalidPlanError",
    "SchedulerAlreadyExistsError",
    "SchedulerError",
    "SchedulerManager",
    "SchedulerNotFoundError",
    "SchedulerRun",
    "SchedulerSnapshot",
    "SchedulerState",
    "SchedulerStore",
    "TaskExecutionRecord",
    "TaskExecutionState",
    "TaskNotFoundError",
    "dependency_resolver",
    "events",
]
