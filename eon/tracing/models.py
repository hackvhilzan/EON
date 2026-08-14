"""
eon.tracing.models
===================
Modelos de tracing distribuido (OTel-shaped, ligero).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class SpanStatus(str, Enum):
    STARTED = "started"
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _gen_trace_id() -> str:
    return uuid.uuid4().hex


def _gen_span_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Span:
    """Un span individual dentro de un trace.

    Un trace es una jerarquía de spans que representa el flujo
    de una ejecución a través de los componentes del kernel.
    """
    trace_id: str = field(default_factory=_gen_trace_id)
    span_id: str = field(default_factory=_gen_span_id)
    parent_span_id: str | None = None
    execution_id: str = ""
    name: str = ""
    status: SpanStatus = SpanStatus.STARTED
    started_at: str = field(default_factory=_utcnow_iso)
    ended_at: str | None = None
    duration_ms: float = 0.0
    attributes: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    def end(
        self,
        status: SpanStatus = SpanStatus.OK,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.ended_at = _utcnow_iso()
        # Calcular duración
        try:
            start = datetime.fromisoformat(self.started_at)
            end_dt = datetime.fromisoformat(self.ended_at)
            self.duration_ms = (end_dt - start).total_seconds() * 1000.0
        except (ValueError, TypeError):
            pass
        if error:
            self.add_event("error", {"message": error})

    def add_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, payload: dict | None = None) -> None:
        self.events.append(
            {
                "name": name,
                "timestamp": _utcnow_iso(),
                "payload": payload or {},
            }
        )

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "execution_id": self.execution_id,
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Span:
        return cls(
            trace_id=data.get("trace_id", ""),
            span_id=data.get("span_id", ""),
            parent_span_id=data.get("parent_span_id"),
            execution_id=data.get("execution_id", ""),
            name=data.get("name", ""),
            status=SpanStatus(data.get("status", "started")),
            started_at=data.get("started_at", _utcnow_iso()),
            ended_at=data.get("ended_at"),
            duration_ms=data.get("duration_ms", 0.0),
            attributes=data.get("attributes", {}),
            events=data.get("events", []),
        )


@dataclass
class Trace:
    """Colección de spans de una ejecución."""
    trace_id: str
    execution_id: str = ""
    spans: list[Span] = field(default_factory=list)

    def total_duration_ms(self) -> float:
        """Duración total: desde el primer span hasta el último."""
        if not self.spans:
            return 0.0
        durations = [s.duration_ms for s in self.spans if s.duration_ms > 0]
        return max(durations) if durations else 0.0

    def span_count(self) -> int:
        return len(self.spans)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
            "span_count": self.span_count(),
            "total_duration_ms": self.total_duration_ms(),
            "spans": [s.to_dict() for s in self.spans],
        }
