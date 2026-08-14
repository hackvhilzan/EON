"""
eon.workspace
===============
Implementación de Fase 11, según `WORKSPACE.md` (contrato congelado
v1.0.1). El Workspace no pertenece al Kernel (WORKSPACE.md §0, §10.12;
`VISION_AND_ROADMAP.md` §11, §18): dominio aislado, no importa
`eon.objectives`, `eon.planner`, `eon.scheduler`, `eon.workers` ni
`eon.verifier`.
"""

from __future__ import annotations

from . import events
from .artifacts import ArtifactRegistry
from .exceptions import (
    ArtifactOwnershipError,
    IllegalWorkspaceTransitionError,
    InvalidWorkspaceError,
    WorkspaceAlreadyExistsError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceNotTerminalError,
    WorkspacePathEscapeError,
    WorkspaceRecoveryError,
)
from .layout import SUBDIRECTORIOS, WorkspaceLayout
from .models import NO_TERMINALES, TERMINALES, Workspace, WorkspaceState
from .snapshot import WorkspaceSnapshot, cargar_snapshot, guardar_snapshot
from .workspace import WorkspaceManager
from .workspace_store import FileWorkspaceStore, InMemoryWorkspaceStore, WorkspaceStore

__all__ = [
    "Workspace",
    "WorkspaceState",
    "TERMINALES",
    "NO_TERMINALES",
    "WorkspaceManager",
    "WorkspaceStore",
    "InMemoryWorkspaceStore",
    "FileWorkspaceStore",
    "WorkspaceLayout",
    "SUBDIRECTORIOS",
    "WorkspaceSnapshot",
    "guardar_snapshot",
    "cargar_snapshot",
    "ArtifactRegistry",
    "events",
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspaceAlreadyExistsError",
    "InvalidWorkspaceError",
    "IllegalWorkspaceTransitionError",
    "WorkspaceNotTerminalError",
    "WorkspacePathEscapeError",
    "WorkspaceRecoveryError",
    "ArtifactOwnershipError",
]
