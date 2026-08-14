"""
eon.coordinator
=================
Implementación de la Fase 13, según la ORDEN MAESTRA — FASE 13
(Coordinator, Kernel Orchestrator v1.0). El Coordinator sí pertenece al
Kernel (`VISION_AND_ROADMAP.md` §11, §18) pero es aislado en su propio
dominio: no importa el código fuente de `eon.objectives`, `eon.planner`,
`eon.scheduler`, `eon.verifier`, `eon.workspace` ni `eon.package` -- solo
sus interfaces públicas, expresadas aquí como `typing.Protocol` en
`ports.py` (ver informe de la Fase 13, "Decisiones de API").
"""

from __future__ import annotations

from . import events
from .coordinator_store import (
    CoordinatorStore,
    FileCoordinatorStore,
    InMemoryCoordinatorStore,
)
from .exceptions import (
    CoordinatorError,
    CoordinatorImmutableError,
    CoordinatorNotFoundError,
    CoordinatorPathEscapeError,
    CoordinatorRecoveryError,
    DelegationError,
    IllegalCoordinatorTransitionError,
    RetriesExhaustedError,
)
from .layout import SUBDIRECTORIOS, CoordinatorLayout
from .manager import CoordinatorManager
from .models import (
    NO_TERMINALES,
    TERMINALES,
    CoordinatorExecution,
    CoordinatorState,
)
from .ports import (
    EventBusPort,
    ObjectiveDescriptorRef,
    ObjectivesPort,
    ObjetivoRef,
    PackagePort,
    PackageRef,
    PlannerPort,
    PlanRef,
    SchedulerPort,
    TaskGenerationPort,
    TaskSpecRef,
    VerificationResultRef,
    VerifierPort,
    WorkspacePort,
    WorkspaceRefPort,
)
from .snapshot import CoordinatorSnapshot, cargar_snapshot, guardar_snapshot

__all__ = [
    "CoordinatorExecution",
    "CoordinatorState",
    "TERMINALES",
    "NO_TERMINALES",
    "CoordinatorManager",
    "CoordinatorStore",
    "InMemoryCoordinatorStore",
    "FileCoordinatorStore",
    "CoordinatorLayout",
    "SUBDIRECTORIOS",
    "CoordinatorSnapshot",
    "guardar_snapshot",
    "cargar_snapshot",
    "events",
    "CoordinatorError",
    "CoordinatorNotFoundError",
    "IllegalCoordinatorTransitionError",
    "CoordinatorImmutableError",
    "CoordinatorPathEscapeError",
    "CoordinatorRecoveryError",
    "DelegationError",
    "RetriesExhaustedError",
    "ObjectivesPort",
    "PlannerPort",
    "SchedulerPort",
    "VerifierPort",
    "WorkspacePort",
    "PackagePort",
    "EventBusPort",
    "TaskGenerationPort",
    "ObjetivoRef",
    "PlanRef",
    "VerificationResultRef",
    "WorkspaceRefPort",
    "PackageRef",
    "ObjectiveDescriptorRef",
    "TaskSpecRef",
]
