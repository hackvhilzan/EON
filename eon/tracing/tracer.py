"""
eon.tracing.tracer
===================
Tracer ligero con propagación de contexto via contextvars.

No usa OpenTelemetry SDK. Es OTel-shaped: trace_id, span_id,
parent_span_id, attributes, events. El exportador OTel real se
añade en Fase 12.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from .models import Span, SpanStatus
from .store import SQLiteTraceStore

logger = logging.getLogger("eon.tracing")

# Context var para el span actual (propagación jerárquica)
_current_span: ContextVar[Span | None] = ContextVar("eon_current_span", default=None)


class Tracer:
    """Tracer ligero para EON.

    Uso:
        tracer = Tracer(trace_store)
        with tracer.start_span("coordinator.run", execution_id="exec-1") as span:
            span.add_attribute("objective", "test")
            # ... trabajo ...
            # hijos heredan trace_id y parent_span_id automáticamente
            with tracer.start_span("planner.generate") as child:
                ...
    """

    def __init__(self, trace_store: SQLiteTraceStore | None = None) -> None:
        self._store = trace_store

    def start_span(
        self,
        name: str,
        execution_id: str = "",
        attributes: dict | None = None,
    ) -> SpanContext:
        """Inicia un span. Si hay un span padre en el contexto, hereda trace_id.

        Devuelve un context manager que finaliza el span al salir.
        """
        parent = _current_span.get()

        if parent is not None:
            trace_id = parent.trace_id
            parent_span_id = parent.span_id
            if not execution_id:
                execution_id = parent.execution_id
        else:
            trace_id = None  # Span raíz genera su propio trace_id
            parent_span_id = None

        span = Span(
            trace_id=trace_id or "",
            parent_span_id=parent_span_id,
            execution_id=execution_id,
            name=name,
        )
        if not span.trace_id:
            # Generar trace_id si es raíz
            from .models import _gen_trace_id

            span.trace_id = _gen_trace_id()

        if attributes:
            for k, v in attributes.items():
                span.add_attribute(k, v)

        span.status = SpanStatus.STARTED

        # Persistir span inicial
        if self._store is not None:
            self._store.save_span(span)

        return SpanContext(span, self, self._store)


class SpanContext:
    """Context manager para un span activo.

    Propaga el span via contextvar para que los spans hijos
    hereden trace_id y parent_span_id automáticamente.
    """

    def __init__(
        self,
        span: Span,
        tracer: Tracer,
        store: SQLiteTraceStore | None,
    ) -> None:
        self._span = span
        self._store = store
        self._token = None

    @property
    def span(self) -> Span:
        return self._span

    def __enter__(self) -> Span:
        self._token = _current_span.set(self._span)
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self._span.end(
                status=SpanStatus.ERROR,
                error=f"{exc_type.__name__}: {exc_val}" if exc_val else "",
            )
        else:
            self._span.end(status=SpanStatus.OK)

        if self._store is not None:
            self._store.save_span(self._span)

        if self._token is not None:
            _current_span.reset(self._token)


def get_current_span() -> Span | None:
    """Obtiene el span activo en el contexto actual."""
    return _current_span.get()
