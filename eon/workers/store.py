"""
eon.workers.store
===================
Persistencia pura de Workers. Sin lógica de dominio.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from .models import Worker


class WorkerStore(ABC):
    """Persistencia pura de Workers."""

    @abstractmethod
    def create(self, worker: Worker) -> Worker: ...

    @abstractmethod
    def get(self, worker_id: str) -> Worker | None: ...

    @abstractmethod
    def update(self, worker: Worker) -> Worker: ...

    @abstractmethod
    def list(self) -> list[Worker]: ...

    @abstractmethod
    def list_idle(self) -> list[Worker]: ...


class InMemoryWorkerStore(WorkerStore):
    """Implementación en memoria de WorkerStore."""

    def __init__(self) -> None:
        self._workers: dict[str, Worker] = {}
        self._lock = threading.Lock()

    def create(self, worker: Worker) -> Worker:
        with self._lock:
            self._workers[worker.id] = worker
            return worker

    def get(self, worker_id: str) -> Worker | None:
        with self._lock:
            return self._workers.get(worker_id)

    def update(self, worker: Worker) -> Worker:
        with self._lock:
            self._workers[worker.id] = worker
            return worker

    def list(self) -> list[Worker]:
        with self._lock:
            return list(self._workers.values())

    def list_idle(self) -> list[Worker]:
        with self._lock:
            return [w for w in self._workers.values() if w.estado.value == "idle"]
