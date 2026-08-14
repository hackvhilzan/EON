"""
eon.objectives
=================
Fase 7 -- Objective Engine. La Fase 6 (OBJECTIVES.md) definió qué es un
Objetivo; este paquete implementa cómo vive: máquina de estados, dependencias,
verificación y el único punto de escritura (ObjectiveManager).
"""

from __future__ import annotations

from . import events
from .dependency_graph import DependencyGraph
from .events import EventBus
from .exceptions import (
    CyclicDependencyError,
    IllegalTransitionError,
    InvalidDecompositionError,
    InvalidHierarchyError,
    InvalidObjectiveError,
    ObjectiveError,
    ObjectiveNotFoundError,
    RetryLimitExceededError,
    TerminalObjectiveError,
)
from .models import Objective, ObjectiveHistoryEntry, ObjectiveOrigin, ObjectiveState
from .objective_manager import ObjectiveManager
from .objective_store import InMemoryObjectiveStore, ObjectiveStore
from .state_machine import ESTADOS_CANCELABLES, TRANSICIONES, validar_cancelacion, validar_transicion
from .verifier import ResultadoJudge, VerificationResult, Verifier

__all__ = [
    "EventBus",
    "Objective",
    "ObjectiveState",
    "ObjectiveOrigin",
    "ObjectiveHistoryEntry",
    "ObjectiveStore",
    "InMemoryObjectiveStore",
    "ObjectiveManager",
    "DependencyGraph",
    "Verifier",
    "VerificationResult",
    "ResultadoJudge",
    "validar_transicion",
    "validar_cancelacion",
    "TRANSICIONES",
    "ESTADOS_CANCELABLES",
    "events",
    "ObjectiveError",
    "ObjectiveNotFoundError",
    "InvalidObjectiveError",
    "IllegalTransitionError",
    "CyclicDependencyError",
    "InvalidHierarchyError",
    "InvalidDecompositionError",
    "RetryLimitExceededError",
    "TerminalObjectiveError",
]
