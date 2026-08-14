"""
eon.hitl.models
================
Modelos de Human-in-the-Loop (HITL) para interrupciones persistentes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class HITLStatus(str, Enum):
    """Estados válidos de una interrupción HITL."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    RESUMED = "resumed"
    EXPIRED = "expired"


# Transiciones válidas
_VALID_TRANSITIONS: dict[HITLStatus, set[HITLStatus]] = {
    HITLStatus.PENDING: {HITLStatus.APPROVED, HITLStatus.DENIED, HITLStatus.EXPIRED},
    HITLStatus.APPROVED: {HITLStatus.RESUMED},
    HITLStatus.DENIED: set(),
    HITLStatus.RESUMED: set(),
    HITLStatus.EXPIRED: set(),
}


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class HITLInterrupt:
    """Interrupción persistente para aprobación humana.

    Flujo:
        PENDING → APPROVED → RESUMED  (aprobado y reanudado)
        PENDING → DENIED               (denegado, task falla)
        PENDING → EXPIRED              (timeout automático)
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    execution_id: str = ""
    task_id: str = ""
    tool_name: str = ""
    reason: str = ""
    payload: dict = field(default_factory=dict)
    checkpoint_id: str | None = None
    status: HITLStatus = HITLStatus.PENDING
    created_at: str = field(default_factory=_utcnow_iso)
    resolved_at: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None

    def can_transition_to(self, new_status: HITLStatus) -> bool:
        return new_status in _VALID_TRANSITIONS.get(self.status, set())

    def transition_to(
        self,
        new_status: HITLStatus,
        decided_by: str | None = None,
        decision_reason: str | None = None,
    ) -> None:
        if not self.can_transition_to(new_status):
            raise ValueError(f"Transición inválida: {self.status.value} → {new_status.value}")
        self.status = new_status
        self.resolved_at = _utcnow_iso()
        if decided_by:
            self.decided_by = decided_by
        if decision_reason:
            self.decision_reason = decision_reason

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "reason": self.reason,
            "payload": self.payload,
            "checkpoint_id": self.checkpoint_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "decided_by": self.decided_by,
            "decision_reason": self.decision_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> HITLInterrupt:
        return cls(
            id=data["id"],
            execution_id=data.get("execution_id", ""),
            task_id=data.get("task_id", ""),
            tool_name=data.get("tool_name", ""),
            reason=data.get("reason", ""),
            payload=data.get("payload", {}),
            checkpoint_id=data.get("checkpoint_id"),
            status=HITLStatus(data.get("status", "pending")),
            created_at=data.get("created_at", _utcnow_iso()),
            resolved_at=data.get("resolved_at"),
            decided_by=data.get("decided_by"),
            decision_reason=data.get("decision_reason"),
        )


@dataclass
class HITLDecision:
    """Record de la decisión humana sobre una interrupción."""

    interrupt_id: str
    decision: HITLStatus
    decided_by: str
    reason: str = ""
    decided_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict:
        return {
            "interrupt_id": self.interrupt_id,
            "decision": self.decision.value,
            "decided_by": self.decided_by,
            "reason": self.reason,
            "decided_at": self.decided_at,
        }
