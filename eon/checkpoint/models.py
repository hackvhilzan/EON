"""Modelos de checkpoint y semantic snapshot."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class CheckpointKind(str, Enum):
    """Tipo de checkpoint."""

    MANUAL = "manual"
    MILESTONE = "milestone"          # hito: plan creado, task completada, etc.
    EVERY_TASK = "every_task"        # después de cada task
    PRE_INTERRUPT = "pre_interrupt"  # antes de una pausa HITL
    RECOVERY = "recovery"            # punto de recuperación automática


@dataclass
class SemanticSnapshot:
    """Estado semántico de una ejecución en un instante.

    Captura el "por qué" del estado, no solo el "qué".
    Todos los campos son deterministas — extraídos de los modelos
    existentes, nunca inferidos por LLM.
    """

    intent: str = ""
    success_criteria: str = ""
    assumptions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    pending_decision: str | None = None
    confidence: float = 0.0
    cost_so_far: float = 0.0
    why_here: str = ""
    objective_state: str = ""
    plan_state: str = ""
    scheduler_progress: str = ""
    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    artifacts: list[str] = field(default_factory=list)
    guardrail_denials: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "success_criteria": self.success_criteria,
            "assumptions": list(self.assumptions),
            "constraints": list(self.constraints),
            "risks": list(self.risks),
            "evidence": dict(self.evidence),
            "pending_decision": self.pending_decision,
            "confidence": self.confidence,
            "cost_so_far": self.cost_so_far,
            "why_here": self.why_here,
            "objective_state": self.objective_state,
            "plan_state": self.plan_state,
            "scheduler_progress": self.scheduler_progress,
            "tasks_total": self.tasks_total,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "artifacts": list(self.artifacts),
            "guardrail_denials": list(self.guardrail_denials),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticSnapshot:
        return cls(
            intent=d.get("intent", ""),
            success_criteria=d.get("success_criteria", ""),
            assumptions=list(d.get("assumptions", [])),
            constraints=list(d.get("constraints", [])),
            risks=list(d.get("risks", [])),
            evidence=dict(d.get("evidence", {})),
            pending_decision=d.get("pending_decision"),
            confidence=d.get("confidence", 0.0),
            cost_so_far=d.get("cost_so_far", 0.0),
            why_here=d.get("why_here", ""),
            objective_state=d.get("objective_state", ""),
            plan_state=d.get("plan_state", ""),
            scheduler_progress=d.get("scheduler_progress", ""),
            tasks_total=d.get("tasks_total", 0),
            tasks_completed=d.get("tasks_completed", 0),
            tasks_failed=d.get("tasks_failed", 0),
            artifacts=list(d.get("artifacts", [])),
            guardrail_denials=list(d.get("guardrail_denials", [])),
        )


@dataclass
class Checkpoint:
    """Checkpoint del estado completo de una ejecución.

    Anclado al EventStore via event_seq: el checkpoint captura el estado
    en el momento en que el EventStore tenía ese número de secuencia.
    Para recuperar estado completo: cargar checkpoint + replay eventos
    posteriores a event_seq.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    execution_id: str = ""
    event_seq: int = 0
    kind: CheckpointKind = CheckpointKind.MANUAL
    stores_state: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    semantic: SemanticSnapshot | None = None
    cost_so_far: float = 0.0
    hash: str = ""
    created_at: str = field(default_factory=_ahora)
    reason: str = ""

    def compute_hash(self) -> str:
        """Calcula hash SHA-256 del estado completo para integridad verificable.

        Incluye TODOS los campos del checkpoint:
        - id, execution_id, event_seq, kind, reason, created_at
        - stores_state, artifacts, cost_so_far
        - semantic (intent, assumptions, risks, evidence, TODO)

        Cualquier alteración de cualquier campo (incluido semantic)
        invalida el hash.
        """
        payload = json.dumps(
            {
                "id": self.id,
                "execution_id": self.execution_id,
                "event_seq": self.event_seq,
                "kind": self.kind.value,
                "reason": self.reason,
                "created_at": self.created_at,
                "stores_state": self.stores_state,
                "artifacts": self.artifacts,
                "cost_so_far": self.cost_so_far,
                "semantic": self.semantic.to_dict() if self.semantic else None,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        self.hash = hashlib.sha256(payload.encode()).hexdigest()
        return self.hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "execution_id": self.execution_id,
            "event_seq": self.event_seq,
            "kind": self.kind.value,
            "stores_state": self.stores_state,
            "artifacts": self.artifacts,
            "semantic": self.semantic.to_dict() if self.semantic else None,
            "cost_so_far": self.cost_so_far,
            "hash": self.hash,
            "created_at": self.created_at,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        semantic = None
        if d.get("semantic"):
            semantic = SemanticSnapshot.from_dict(d["semantic"])
        return cls(
            id=d["id"],
            execution_id=d["execution_id"],
            event_seq=d.get("event_seq", 0),
            kind=CheckpointKind(d.get("kind", "manual")),
            stores_state=d.get("stores_state", {}),
            artifacts=d.get("artifacts", {}),
            semantic=semantic,
            cost_so_far=d.get("cost_so_far", 0.0),
            hash=d.get("hash", ""),
            created_at=d.get("created_at", _ahora()),
            reason=d.get("reason", ""),
        )
