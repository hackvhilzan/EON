"""
eon.workers.registry
======================
WorkerRegistry — busca Workers compatibles con una capability.
"""

from __future__ import annotations

from .models import Worker, WorkerState
from .store import WorkerStore


class WorkerRegistry:
    """Busca Workers IDLE que soporten una capability dada."""

    def __init__(self, store: WorkerStore) -> None:
        self._store = store

    def find_compatible(self, capability_id: str) -> Worker | None:
        """Devuelve el primer Worker IDLE que soporta `capability_id`,
        o `None` si no hay ninguno disponible."""
        for worker in self._store.list():
            if worker.estado == WorkerState.IDLE and worker.soporta(capability_id):
                return worker
        return None
