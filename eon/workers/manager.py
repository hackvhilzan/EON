"""
eon.workers.manager
=====================
WorkerManager — único punto de escritura sobre Workers.

Registra Workers con sus capabilities, los reserva (IDLE → BUSY) y los
libera (BUSY → IDLE). No ejecuta Tasks ni conoce Tools ni LLM.
"""

from __future__ import annotations

from typing import Any

from .models import Worker, WorkerState
from .store import WorkerStore


class WorkerManager:
    """Gestiona el ciclo de vida de los Workers."""

    def __init__(self, store: WorkerStore, event_bus: Any) -> None:
        self._store = store
        self._events = event_bus

    def registrar(self, capabilities: list[str], nombre: str = "", worker_id: str | None = None) -> Worker:
        """Registra un nuevo Worker con las capabilities indicadas."""
        import uuid as _uuid

        wid = worker_id or str(_uuid.uuid4())
        worker = Worker(
            capabilities=tuple(capabilities),
            id=wid,
            nombre=nombre,
        )
        self._store.create(worker)
        return worker

    def reservar(self, worker_id: str, task_id: str | None = None) -> Worker:
        """Marca un Worker como BUSY. Debe estar en IDLE."""
        worker = self._store.get(worker_id)
        if worker is None:
            raise ValueError(f"Worker no encontrado: '{worker_id}'.")
        if worker.estado != WorkerState.IDLE:
            raise ValueError(f"Worker '{worker_id}' no está IDLE (estado: {worker.estado}).")
        worker.estado = WorkerState.BUSY
        worker.task_actual = task_id
        self._store.update(worker)
        return worker

    def liberar(self, worker_id: str) -> Worker:
        """Marca un Worker como IDLE. Debe estar en BUSY."""
        worker = self._store.get(worker_id)
        if worker is None:
            raise ValueError(f"Worker no encontrado: '{worker_id}'.")
        if worker.estado != WorkerState.BUSY:
            raise ValueError(f"Worker '{worker_id}' no está BUSY (estado: {worker.estado}).")
        worker.estado = WorkerState.IDLE
        worker.task_actual = None
        self._store.update(worker)
        return worker

    def obtener(self, worker_id: str) -> Worker | None:
        return self._store.get(worker_id)

    def listar(self) -> list[Worker]:
        return self._store.list()
