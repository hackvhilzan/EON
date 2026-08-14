"""
eon.workers.dispatcher
========================
Dispatcher — asigna una Task a un Worker compatible y la ejecuta.

El Dispatcher es el puente entre el Scheduler (que decide QUÉ Task puede
ejecutarse) y el TaskExecutor (que la ejecuta). Busca un Worker IDLE que
soporte la `capability_id` de la Task, lo reserva (IDLE → BUSY), ejecuta
la Task, y libera el Worker (BUSY → IDLE).
"""

from __future__ import annotations

import logging
from typing import Any

from .executor import TaskExecutor
from .manager import WorkerManager
from .registry import WorkerRegistry

logger = logging.getLogger("eon.workers.dispatcher")


class NoCompatibleWorkerError(Exception):
    """No hay ningún Worker IDLE que soporte la capability de la Task."""


class Dispatcher:
    """Despacha Tasks a Workers compatibles."""

    def __init__(
        self,
        registry: WorkerRegistry,
        manager: WorkerManager,
        executor: TaskExecutor,
    ) -> None:
        self._registry = registry
        self._manager = manager
        self._executor = executor

    def despachar(self, task: Any) -> None:
        """Busca un Worker compatible, lo reserva, ejecuta la Task, y lo libera.

        Lanza `NoCompatibleWorkerError` si no hay Worker disponible.
        """
        capability_id = task.capability_id
        worker = self._registry.find_compatible(capability_id)
        if worker is None:
            raise NoCompatibleWorkerError(f"No hay Worker IDLE para capability '{capability_id}'.")

        self._manager.reservar(worker.id, task_id=task.id)
        try:
            self._executor.ejecutar(worker.id, task)
        finally:
            self._manager.liberar(worker.id)
