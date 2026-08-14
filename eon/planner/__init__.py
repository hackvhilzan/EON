"""
eon.planner
=============
Implementación de Fase 8, según `PLANNER.md` (contrato congelado v1.0).

Dominio completamente aislado: no importa nada de `eon.objectives`, `eon.core`,
`eon.tools` ni ningún proveedor LLM. `objective_id` viaja como un simple
`str` -- PlannerManager nunca resuelve ni valida ese id contra un
ObjectiveStore real (eso violaría PLANNER.md §0: "no conoce infraestructura").
Quien integra ambos dominios (Core, en fases posteriores) es responsable de
que el `objective_id` que le pasa a este paquete corresponda a un Objetivo
real.

Superficie pública mínima -- lo que necesita quien integra este paquete desde
fuera:
"""

from __future__ import annotations

from . import planner_events as events
from .models import Plan, PlanHistoryEntry, PlanOrigin, PlanState
from .plan_selector import menor_numero_de_tasks, primero, seleccionar
from .planner import PlannerManager
from .planner_exceptions import (
    IllegalPlanTransitionError,
    InvalidPlanError,
    InvalidTaskGraphError,
    PlannerError,
    PlanNotFoundError,
    TerminalPlanError,
)
from .planner_store import InMemoryPlannerStore, PlannerStore
from .strategy_builder import construir, construir_candidatos
from .task import Task
from .task_graph import TaskGraph

__all__ = [
    "Plan",
    "PlanHistoryEntry",
    "PlanOrigin",
    "PlanState",
    "Task",
    "TaskGraph",
    "PlannerManager",
    "PlannerStore",
    "InMemoryPlannerStore",
    "construir",
    "construir_candidatos",
    "seleccionar",
    "primero",
    "menor_numero_de_tasks",
    "events",
    "PlannerError",
    "PlanNotFoundError",
    "InvalidPlanError",
    "IllegalPlanTransitionError",
    "TerminalPlanError",
    "InvalidTaskGraphError",
]
