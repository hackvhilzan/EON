"""
eon.planning
==============
Planificación inteligente: multi-plan, scoring, simulación,
auto-replanificación y descomposición de objetivos.

El PlanScorer evalúa planes con criterios deterministas.
El PlanSimulator predice éxito/coste/tiempo.
El ObjectiveDecomposer descompone objetivos complejos.
El AutoReplanner hace replanificación contextual tras rechazos.
"""

from __future__ import annotations

from .decomposer import ObjectiveDecomposer
from .models import PlanScore, PlanSimulation, ReplanContext, SubObjective
from .replanning import AutoReplanner
from .scoring import PlanScorer
from .simulator import PlanSimulator

__all__ = [
    "PlanScore",
    "PlanSimulation",
    "SubObjective",
    "ReplanContext",
    "PlanScorer",
    "PlanSimulator",
    "ObjectiveDecomposer",
    "AutoReplanner",
]
