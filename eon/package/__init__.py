"""
eon.package
=============
Fase 12 — Package. Construye el resultado final empaquetado.

El Package es el último paso del ciclo del Kernel: toma un Workspace
completado y produce un Package en estado `ready` que contiene los
artefactos del Workspace. El Coordinator solicita el Package tras
recibir `workspace_completado`.

Dominio aislado: no importa `eon.objectives`, `eon.planner`,
`eon.scheduler` ni `eon.workers`. Su única conexión con el resto del
Kernel es el `WorkspaceRef` que recibe del Coordinator y el `EventBus`.
"""

from __future__ import annotations

from . import events
from .models import Package, PackageState, WorkspaceRef
from .package import PackageManager
from .package_store import InMemoryPackageStore, PackageStore

__all__ = [
    "Package",
    "PackageState",
    "WorkspaceRef",
    "PackageManager",
    "PackageStore",
    "InMemoryPackageStore",
    "events",
]
