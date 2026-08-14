"""
Modelos de dominio del Scheduler (Fase 9).

El Scheduler no ejecuta Tasks, no conoce Tools ni infraestructura. Su único
propósito es mantener, para un Plan activo, qué Task puede ejecutarse en
cada momento a partir del grafo de dependencias declarado en el Plan.

Decisión de diseño (fuera del contrato, necesaria para implementar):
el Scheduler NO importa `eon.planner`. Consume cualquier objeto "Plan-like"
duck-typed (con `.id` y `.tasks`, donde cada Task-like tiene `.id` y
`.depende_de`) y construye, a partir de él, sus propios registros
(`TaskExecutionRecord`). Esto reutiliza el ENFOQUE algorítmico de
`TaskGraph` (validación de grafo + detección de ciclos) sin acoplar el
paquete a los tipos concretos del Planner, manteniendo al Scheduler como
un dominio autocontenido — igual que Objectives y Planner lo son entre sí.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _ahora() -> datetime:
    return datetime.now(UTC)


class SchedulerState(str, Enum):
    """Estado global de la ejecución de un Plan dentro del Scheduler."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"

    @property
    def es_terminal(self) -> bool:
        return self is SchedulerState.STOPPED


class TaskExecutionState(str, Enum):
    """Estado de ejecución de una Task dentro del Scheduler.

    Nota: distinto del estado de la Task dentro del Plan (Planner). El
    Scheduler nunca modifica el Plan; este estado vive exclusivamente en
    el dominio del Scheduler.
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"

    @property
    def es_terminal(self) -> bool:
        return self in (
            TaskExecutionState.COMPLETED,
            TaskExecutionState.FAILED,
            TaskExecutionState.CANCELLED,
            TaskExecutionState.BLOCKED,
        )


@dataclass
class TaskExecutionRecord:
    """Registro mutable de ejecución de una Task dentro de un Plan.

    `depende_de` se congela como tupla en la creación (orden estable,
    necesario para resolución determinista de dependencias).
    """

    task_id: str
    depende_de: tuple[str, ...] = field(default_factory=tuple)
    estado: TaskExecutionState = TaskExecutionState.PENDING


@dataclass
class SchedulerRun:
    """Agregado interno: la ejecución de un Plan dentro del Scheduler.

    No es parte del contrato explícito de Fase 9 (que solo exige
    `SchedulerSnapshot` como salida), pero es necesario como estado
    persistido en el Store. Sigue el mismo patrón que `Plan` en el
    Planner: mutable únicamente en `estado`, `tasks` (estado interno de
    cada Task) e `historial`.
    """

    plan_id: str
    tasks: dict[str, TaskExecutionRecord]
    orden: tuple[str, ...]
    estado: SchedulerState = SchedulerState.IDLE
    creado_en: datetime = field(default_factory=_ahora)
    actualizado_en: datetime = field(default_factory=_ahora)
    historial: list[dict[str, Any]] = field(default_factory=list)

    def registrar(
        self,
        evento: str,
        *,
        de: str | None = None,
        a: str | None = None,
        motivo: str | None = None,
        **extra: Any,
    ) -> None:
        entrada: dict[str, Any] = {
            "evento": evento,
            "de": de,
            "a": a,
            "motivo": motivo,
            "en": _ahora(),
        }
        if extra:
            entrada["extra"] = extra
        self.historial.append(entrada)
        self.actualizado_en = entrada["en"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "tasks": {
                tid: {"task_id": rec.task_id, "depende_de": list(rec.depende_de), "estado": rec.estado.value}
                for tid, rec in self.tasks.items()
            },
            "orden": list(self.orden),
            "estado": self.estado.value,
            "creado_en": self.creado_en.isoformat() if isinstance(self.creado_en, datetime) else str(self.creado_en),
            "actualizado_en": self.actualizado_en.isoformat() if isinstance(self.actualizado_en, datetime) else str(self.actualizado_en),
            "historial": list(self.historial),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SchedulerRun:
        tasks: dict[str, TaskExecutionRecord] = {}
        for tid, rec in (d.get("tasks") or {}).items():
            tasks[tid] = TaskExecutionRecord(
                task_id=rec["task_id"],
                depende_de=tuple(rec.get("depende_de", [])),
                estado=TaskExecutionState(rec.get("estado", "pending")),
            )
        creado = d.get("creado_en", _ahora())
        actualizado = d.get("actualizado_en", _ahora())
        if isinstance(creado, str):
            with contextlib.suppress(ValueError):
                creado = datetime.fromisoformat(creado)
        if isinstance(actualizado, str):
            with contextlib.suppress(ValueError):
                actualizado = datetime.fromisoformat(actualizado)
        return cls(
            plan_id=d["plan_id"],
            tasks=tasks,
            orden=tuple(d.get("orden", [])),
            estado=SchedulerState(d.get("estado", "idle")),
            creado_en=creado,
            actualizado_en=actualizado,
            historial=list(d.get("historial", [])),
        )


@dataclass(frozen=True)
class SchedulerSnapshot:
    """Foto de solo lectura del estado de un Plan dentro del Scheduler.

    Los campos son exactamente los exigidos por el contrato de Fase 9.
    Las Tasks en BLOCKED o CANCELLED no pertenecen a ninguna de las cinco
    colas (decisión explícita: el contrato solo define estas cinco).
    """

    plan_id: str
    estado: SchedulerState
    cola_ready: tuple[str, ...]
    cola_pending: tuple[str, ...]
    cola_running: tuple[str, ...]
    cola_completed: tuple[str, ...]
    cola_failed: tuple[str, ...]
    creado_en: datetime
    actualizado_en: datetime
