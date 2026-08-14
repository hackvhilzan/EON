"""
eon.persistence.store_registry
=================================
Factory que crea un conjunto coherente de stores para un backend dado.

Uso:
    from eon.persistence import create_stores

    # SQLite (persistente)
    stores = create_stores(backend="sqlite", db_path="/data/eon.db")

    # InMemory (compatibilidad, para tests)
    stores = create_stores(backend="memory")

    # Usar los stores
    runtime = KernelRuntime(
        objective_store=stores.objective_store,
        planner_store=stores.planner_store,
        ...
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .event_store import EventStore
from .sqlite_engine import SQLiteEngine
from .sqlite_stores import (
    SQLiteCoordinatorStore,
    SQLiteObjectiveStore,
    SQLitePackageStore,
    SQLitePlannerStore,
    SQLiteSchedulerStore,
    SQLiteWorkerStore,
    SQLiteWorkspaceStore,
)


@dataclass
class StoreRegistry:
    """Contenedor de todos los stores + engine + event_store."""

    engine: SQLiteEngine | None
    coordinator_store: Any
    objective_store: Any
    planner_store: Any
    scheduler_store: Any
    workspace_store: Any
    package_store: Any
    worker_store: Any
    event_store: EventStore | None

    def close(self) -> None:
        """Cierra conexiones."""
        if self.engine is not None:
            self.engine.close()

    def __enter__(self) -> StoreRegistry:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def create_stores(
    backend: str = "sqlite",
    db_path: str | Path = ".eon_runtime/eon.db",
) -> StoreRegistry:
    """Crea un conjunto de stores para el backend especificado.

    Args:
        backend: "sqlite" (persistente) o "memory" (InMemory, para tests).
        db_path: Ruta al archivo .db (solo SQLite).

    Returns:
        StoreRegistry con todos los stores configurados.
    """
    if backend == "memory":
        from ..coordinator.coordinator_store import InMemoryCoordinatorStore
        from ..objectives.objective_store import InMemoryObjectiveStore
        from ..package.package_store import InMemoryPackageStore
        from ..planner.planner_store import InMemoryPlannerStore
        from ..scheduler.scheduler_store import InMemorySchedulerStore
        from ..workers.store import InMemoryWorkerStore
        from ..workspace.workspace_store import InMemoryWorkspaceStore

        return StoreRegistry(
            engine=None,
            coordinator_store=InMemoryCoordinatorStore(),
            objective_store=InMemoryObjectiveStore(),
            planner_store=InMemoryPlannerStore(),
            scheduler_store=InMemorySchedulerStore(),
            workspace_store=InMemoryWorkspaceStore(),
            package_store=InMemoryPackageStore(),
            worker_store=InMemoryWorkerStore(),
            event_store=None,
        )

    if backend == "sqlite":
        engine = SQLiteEngine(db_path)
        engine.init_schema()

        return StoreRegistry(
            engine=engine,
            coordinator_store=SQLiteCoordinatorStore(engine),
            objective_store=SQLiteObjectiveStore(engine),
            planner_store=SQLitePlannerStore(engine),
            scheduler_store=SQLiteSchedulerStore(engine),
            workspace_store=SQLiteWorkspaceStore(engine),
            package_store=SQLitePackageStore(engine),
            worker_store=SQLiteWorkerStore(engine),
            event_store=EventStore(engine),
        )

    raise ValueError(f"Backend desconocido: '{backend}'. Opciones: 'sqlite', 'memory'.")
