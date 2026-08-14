"""
eon.timetravel.models
======================
Modelos para la reconstrucción de estado en un punto temporal.

ReconstructedState es el resultado de TimeMachine.reconstruct_state().
Es read-only: representa una "fotografía" del kernel en un instante,
no un estado mutable sobre el que se pueda operar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReconstructionMode(str, Enum):
    """Modo de reconstrucción del estado."""

    EXACT = "exact"  # checkpoint base == target_seq, sin eventos posteriores
    CHECKPOINT_PLUS = "checkpoint_plus"  # checkpoint + eventos aplicados (todos conocidos)
    PARTIAL = "partial"  # checkpoint + eventos no aplicados (estado puede diferir)
    EVENTS_ONLY = "events_only"  # sin checkpoint base, solo timeline


@dataclass(frozen=True)
class ReconstructedState:
    """Estado reconstruido del kernel en un event_seq específico.

    Atributos:
        execution_id: ID de la ejecución reconstruida.
        target_event_seq: event_seq solicitado para la reconstrucción.
        base_checkpoint_id: ID del checkpoint usado como base (None si no hay).
        base_checkpoint_seq: event_seq del checkpoint base.
        reconstruction_mode: cómo se construyó el estado (EXACT, CHECKPOINT_PLUS,
            PARTIAL, EVENTS_ONLY).
        complete: True solo si el estado en target_seq es exacto:
            - EXACT: checkpoint en target_seq, sin eventos posteriores.
            - CHECKPOINT_PLUS: checkpoint + todos los eventos aplicados con reducers.
            False si hay eventos no aplicados (PARTIAL) o sin checkpoint (EVENTS_ONLY).
        stores_state: estado de los stores en el momento del checkpoint base
            (dict vacío si no hay checkpoint base).
        semantic: semantic snapshot del checkpoint base (None si no hay).
        events_timeline: lista de eventos entre (base_seq, target_seq].
            Cada evento es un dict con seq, event_type, payload, timestamp.
        applied_events: eventos cuyo tipo tiene un reducer implementado.
        unapplied_events: eventos sin reducer (preservados para transparencia).
        hash_verified: si el hash del checkpoint base es válido.
        total_events_replayed: número total de eventos en el timeline.
    """

    execution_id: str = ""
    target_event_seq: int = 0
    base_checkpoint_id: str | None = None
    base_checkpoint_seq: int = 0
    reconstruction_mode: ReconstructionMode = ReconstructionMode.EVENTS_ONLY
    complete: bool = False
    stores_state: dict[str, Any] = field(default_factory=dict)
    semantic: dict[str, Any] | None = None
    events_timeline: list[dict[str, Any]] = field(default_factory=list)
    applied_events: list[dict[str, Any]] = field(default_factory=list)
    unapplied_events: list[dict[str, Any]] = field(default_factory=list)
    hash_verified: bool = False
    total_events_replayed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "target_event_seq": self.target_event_seq,
            "base_checkpoint_id": self.base_checkpoint_id,
            "base_checkpoint_seq": self.base_checkpoint_seq,
            "reconstruction_mode": self.reconstruction_mode.value,
            "complete": self.complete,
            "stores_state": dict(self.stores_state),
            "semantic": self.semantic,
            "events_timeline": list(self.events_timeline),
            "applied_events": list(self.applied_events),
            "unapplied_events": list(self.unapplied_events),
            "hash_verified": self.hash_verified,
            "total_events_replayed": self.total_events_replayed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReconstructedState:
        mode_str = d.get("reconstruction_mode", "events_only")
        try:
            mode = ReconstructionMode(mode_str)
        except ValueError:
            mode = ReconstructionMode.EVENTS_ONLY
        return cls(
            execution_id=d.get("execution_id", ""),
            target_event_seq=d.get("target_event_seq", 0),
            base_checkpoint_id=d.get("base_checkpoint_id"),
            base_checkpoint_seq=d.get("base_checkpoint_seq", 0),
            reconstruction_mode=mode,
            complete=d.get("complete", False),
            stores_state=dict(d.get("stores_state", {})),
            semantic=d.get("semantic"),
            events_timeline=list(d.get("events_timeline", [])),
            applied_events=list(d.get("applied_events", [])),
            unapplied_events=list(d.get("unapplied_events", [])),
            hash_verified=d.get("hash_verified", False),
            total_events_replayed=d.get("total_events_replayed", 0),
        )
