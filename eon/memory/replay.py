"""
eon.memory.replay
===================
Replay de ejecuciones: reproduce una ejecución paso a paso
desde el EventStore.

Útil para debugging, demostraciones y entrenamiento.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("eon.memory.replay")


@dataclass
class ReplayStep:
    """Un paso en el replay de una ejecución."""

    seq: int
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "description": self.description,
        }


@dataclass
class ReplayResult:
    """Resultado de un replay completo."""

    execution_id: str
    steps: list[ReplayStep] = field(default_factory=list)
    total_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "total_events": self.total_events,
            "steps": [s.to_dict() for s in self.steps],
        }


# Descripciones legibles para tipos de evento
_EVENT_DESCRIPTIONS: dict[str, str] = {
    "coordinator.transition": "Coordinator cambió de estado",
    "objective.transition": "Objective cambió de estado",
    "plan.created": "Plan creado",
    "plan.activated": "Plan activado",
    "plan.obsolete": "Plan marcado como obsoleto",
    "scheduler.iniciado": "Scheduler iniciado",
    "scheduler.finalizado": "Scheduler finalizado",
    "task.ready": "Task lista para ejecutar",
    "task.completed": "Task completada",
    "task.failed": "Task falló",
    "workspace.transition": "Workspace cambió de estado",
    "package.transition": "Package cambió de estado",
    "checkpoint.created": "Checkpoint creado",
    "hitl.interrupt.created": "Interrupción HITL creada",
    "hitl.interrupt.resolved": "Interrupción HITL resuelta",
    "execution.forked": "Ejecución bifurcada",
}


class ExecutionReplayer:
    """Reproduce ejecuciones paso a paso desde el EventStore.

    Uso:
        replayer = ExecutionReplayer(event_store)
        replay = replayer.replay("exec-123")
        for step in replay.steps:
            print(f"[{step.seq}] {step.description}")
    """

    def __init__(self, event_store: Any) -> None:
        self._event_store = event_store

    def replay(
        self,
        execution_id: str,
        from_seq: int = 0,
        to_seq: int | None = None,
        event_type_filter: str | None = None,
    ) -> ReplayResult:
        """Reproduce una ejecución desde el EventStore.

        Args:
            execution_id: ID de la ejecución a reproducir.
            from_seq: Secuencia inicial (inclusive).
            to_seq: Secuencia final (inclusive). None = hasta el final.
            event_type_filter: Filtrar por tipo de evento.

        Returns:
            ReplayResult con los pasos del replay.
        """
        events = self._event_store.get_events(
            execution_id=execution_id,
            after_seq=from_seq - 1 if from_seq > 0 else 0,
        )

        steps: list[ReplayStep] = []
        for event in events:
            if to_seq is not None and event.seq > to_seq:
                break
            if event_type_filter and event.event_type != event_type_filter:
                continue

            desc = _EVENT_DESCRIPTIONS.get(
                event.event_type,
                f"Evento: {event.event_type}",
            )

            steps.append(ReplayStep(
                seq=event.seq,
                event_type=event.event_type,
                payload=event.payload if hasattr(event, "payload") else {},
                timestamp=event.timestamp if hasattr(event, "timestamp") else "",
                description=desc,
            ))

        return ReplayResult(
            execution_id=execution_id,
            steps=steps,
            total_events=len(steps),
        )

    def replay_step_by_step(
        self,
        execution_id: str,
        from_seq: int = 0,
    ):
        """Generador que produce pasos uno a uno.

        Útil para streaming en tiempo real.
        """
        events = self._event_store.get_events(
            execution_id=execution_id,
            after_seq=from_seq - 1 if from_seq > 0 else 0,
        )

        for event in events:
            desc = _EVENT_DESCRIPTIONS.get(
                event.event_type,
                f"Evento: {event.event_type}",
            )
            yield ReplayStep(
                seq=event.seq,
                event_type=event.event_type,
                payload=event.payload if hasattr(event, "payload") else {},
                timestamp=event.timestamp if hasattr(event, "timestamp") else "",
                description=desc,
            )
