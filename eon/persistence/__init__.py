"""
eon.persistence
=================
Capa de persistencia durable para el Kernel EON.

Backends:
- SQLite (default): embebido, sin servidor, un solo archivo .db
- InMemory: delega a los stores existentes (compatibilidad)

Uso:
    from eon.persistence import create_stores
    stores = create_stores(backend="sqlite", db_path="eon.db")
    # stores.objective_store, stores.planner_store, etc.

El EventStore permite event sourcing: replay de eventos para
reconstrucción de estado tras un reinicio de proceso.
"""
from __future__ import annotations

from .event_store import EventEntry, EventStore
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
from .store_registry import StoreRegistry, create_stores

__all__ = [
    "SQLiteEngine",
    "SQLiteCoordinatorStore",
    "SQLiteObjectiveStore",
    "SQLitePackageStore",
    "SQLitePlannerStore",
    "SQLiteSchedulerStore",
    "SQLiteWorkerStore",
    "SQLiteWorkspaceStore",
    "EventStore",
    "EventEntry",
    "StoreRegistry",
    "create_stores",
]
