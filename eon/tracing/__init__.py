"""
eon.tracing
============
Tracing distribuido ligero (OTel-shaped) para observabilidad de ejecuciones.
"""
from .models import Span, SpanStatus, Trace
from .store import SQLiteTraceStore
from .tracer import Tracer, get_current_span

__all__ = [
    "Span",
    "SpanStatus",
    "Trace",
    "SQLiteTraceStore",
    "Tracer",
    "get_current_span",
]
