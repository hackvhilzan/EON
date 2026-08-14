"""
eon.forking.models
===================
Modelos para Execution Forking.

ExecutionFork es el registro durable de una bifurcación de ejecución.
Preserva la trazabilidad del parent: qué ejecución originó el fork,
desde qué checkpoint, en qué event_seq, y qué nueva ejecución se creó.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class ForkStatus(str, Enum):
    """Estado de un fork de ejecución."""

    CREATED = "created"        # Fork creado, ejecución no iniciada
    RUNNING = "running"        # Ejecución forked en progreso
    COMPLETED = "completed"    # Ejecución forked finalizada
    FAILED = "failed"          # Ejecución forked falló
    ABANDONED = "abandoned"    # Fork descartado sin ejecutar


@dataclass
class ExecutionFork:
    """Registro durable de una bifurcación de ejecución.

    Atributos:
        id: ID único del fork.
        parent_execution_id: Ejecución original desde la que se bifurca.
        parent_checkpoint_id: Checkpoint desde el que se copió el estado.
        forked_at_seq: event_seq del checkpoint base.
        new_execution_id: ID de la nueva ejecución creada por el fork.
        new_objective: Objetivo opcional modificado para el fork.
        status: Estado actual del fork.
        created_at: Timestamp de creación.
        completed_at: Timestamp de finalización (si aplica).
        metadata: Metadatos adicionales (razón del fork, notas, etc.).
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_execution_id: str = ""
    parent_checkpoint_id: str = ""
    forked_at_seq: int = 0
    new_execution_id: str = ""
    new_objective: str | None = None
    status: ForkStatus = ForkStatus.CREATED
    created_at: str = field(default_factory=_ahora)
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_execution_id": self.parent_execution_id,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "forked_at_seq": self.forked_at_seq,
            "new_execution_id": self.new_execution_id,
            "new_objective": self.new_objective,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionFork:
        return cls(
            id=d["id"],
            parent_execution_id=d.get("parent_execution_id", ""),
            parent_checkpoint_id=d.get("parent_checkpoint_id", ""),
            forked_at_seq=d.get("forked_at_seq", 0),
            new_execution_id=d.get("new_execution_id", ""),
            new_objective=d.get("new_objective"),
            status=ForkStatus(d.get("status", "created")),
            created_at=d.get("created_at", _ahora()),
            completed_at=d.get("completed_at"),
            metadata=dict(d.get("metadata", {})),
        )
