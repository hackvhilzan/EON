"""
eon.workers
=============
Fase 10 — Workers. Ejecutan las Tasks asignadas por el Scheduler.

El Worker no es un dominio del Kernel con Store/Manager/eventos propios
en el sentido de Objectives o Planner: es la capa de ejecución que
recibe una Task, la despacha a un Worker compatible, invoca la
capability inyectada (`EjecutorDeCapability = Callable[[str, dict], bool]`)
y reporta el resultado vía EventBus.

Paquete autocontenido: no importa `eon.planner`, `eon.objectives`,
`eon.scheduler` ni `eon.coordinator`. Su única conexión con el resto
del Kernel es el `EventBus` y el `Callable[[str, dict], bool]` inyectado
por `KernelRuntime`.
"""

from __future__ import annotations

from . import events
from .dispatcher import Dispatcher
from .executor import TaskExecutor
from .manager import WorkerManager
from .models import Worker, WorkerState
from .registry import WorkerRegistry
from .store import InMemoryWorkerStore, WorkerStore

__all__ = [
    "Dispatcher",
    "TaskExecutor",
    "WorkerManager",
    "WorkerRegistry",
    "InMemoryWorkerStore",
    "WorkerStore",
    "Worker",
    "WorkerState",
    "events",
]
