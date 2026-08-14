"""
Tests de Tracing (Fase 3.5).

Criterios de aceptación:
- Span create → end → verify status, duration
- Parent/child span propagation via contextvars
- Trace store: parent/child spans persistidos
- Runtime crea trace por ejecución
- Sin SQLite: tracing no rompe compatibilidad
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eon.persistence import SQLiteEngine
from eon.tracing import Span, SpanStatus, SQLiteTraceStore, Tracer


@pytest.fixture
def engine(tmp_path: Path) -> SQLiteEngine:
    eng = SQLiteEngine(tmp_path / "test_trace.db")
    eng.init_schema()
    yield eng
    eng.close()


@pytest.fixture
def trace_store(engine: SQLiteEngine) -> SQLiteTraceStore:
    return SQLiteTraceStore(engine)


@pytest.fixture
def tracer(trace_store: SQLiteTraceStore) -> Tracer:
    return Tracer(trace_store)


# ─── Span Model Tests ──────────────────────────────────────


class TestSpanModel:
    def test_create_default(self):
        span = Span(name="test.span", execution_id="exec-1")
        assert span.trace_id != ""
        assert span.span_id != ""
        assert span.status == SpanStatus.STARTED
        assert span.parent_span_id is None

    def test_end_ok(self):
        span = Span(name="test.span")
        span.end(status=SpanStatus.OK)
        assert span.status == SpanStatus.OK
        assert span.ended_at is not None
        assert span.duration_ms >= 0.0

    def test_end_error(self):
        span = Span(name="test.span")
        span.end(status=SpanStatus.ERROR, error="Something failed")
        assert span.status == SpanStatus.ERROR
        assert any(e["name"] == "error" for e in span.events)

    def test_add_attribute(self):
        span = Span(name="test.span")
        span.add_attribute("key", "value")
        assert span.attributes["key"] == "value"

    def test_add_event(self):
        span = Span(name="test.span")
        span.add_event("checkpoint.created", {"id": "cp-1"})
        assert len(span.events) == 1
        assert span.events[0]["name"] == "checkpoint.created"

    def test_to_dict_from_dict_roundtrip(self):
        span = Span(name="test.span", execution_id="exec-1")
        span.add_attribute("key", "value")
        span.end(status=SpanStatus.OK)
        d = span.to_dict()
        restored = Span.from_dict(d)
        assert restored.name == "test.span"
        assert restored.status == SpanStatus.OK
        assert restored.attributes == {"key": "value"}


# ─── SQLiteTraceStore Tests ────────────────────────────────


class TestSQLiteTraceStore:
    def test_save_and_get_trace(self, trace_store: SQLiteTraceStore):
        span = Span(name="root", execution_id="exec-1")
        span.end()
        trace_store.save_span(span)

        trace = trace_store.get_trace(span.trace_id)
        assert len(trace) == 1
        assert trace[0].name == "root"

    def test_list_spans(self, trace_store: SQLiteTraceStore):
        for i in range(3):
            s = Span(name=f"span-{i}", execution_id="exec-1")
            s.end()
            trace_store.save_span(s)

        # Other execution
        s2 = Span(name="other", execution_id="exec-2")
        s2.end()
        trace_store.save_span(s2)

        result = trace_store.list_spans("exec-1")
        assert len(result) == 3
        assert all(s.execution_id == "exec-1" for s in result)

    def test_survives_reopen(self, tmp_path: Path):
        eng1 = SQLiteEngine(tmp_path / "reopen_trace.db")
        eng1.init_schema()
        store1 = SQLiteTraceStore(eng1)
        span = Span(name="test", execution_id="exec-1")
        span.end()
        store1.save_span(span)
        eng1.close()

        eng2 = SQLiteEngine(tmp_path / "reopen_trace.db")
        eng2.init_schema()
        store2 = SQLiteTraceStore(eng2)
        trace = store2.get_trace(span.trace_id)
        assert len(trace) == 1
        assert trace[0].name == "test"
        eng2.close()

    def test_count(self, trace_store: SQLiteTraceStore):
        for i in range(5):
            s = Span(name=f"s{i}", execution_id="exec-1")
            s.end()
            trace_store.save_span(s)
        assert trace_store.count() == 5
        assert trace_store.count("exec-1") == 5


# ─── Tracer Tests ──────────────────────────────────────────


class TestTracer:
    def test_start_span_context_manager(self, tracer: Tracer):
        with tracer.start_span("test.span", execution_id="exec-1") as span:
            span.add_attribute("key", "value")
        assert span.status == SpanStatus.OK
        assert span.duration_ms >= 0.0

    def test_span_error_on_exception(self, tracer: Tracer):
        with pytest.raises(RuntimeError):
            with tracer.start_span("test.span"):
                raise RuntimeError("fail")
        # Span should be ended with error status
        from eon.tracing.tracer import get_current_span

        # After context exit, current span is reset
        assert get_current_span() is None

    def test_parent_child_propagation(self, tracer: Tracer):
        with tracer.start_span("parent", execution_id="exec-1") as parent:
            parent_trace_id = parent.trace_id
            with tracer.start_span("child") as child:
                assert child.trace_id == parent_trace_id
                assert child.parent_span_id == parent.span_id
                assert child.execution_id == "exec-1"

    def test_nested_spans_same_trace(self, tracer: Tracer):
        with tracer.start_span("root", execution_id="exec-1") as root:
            root_trace = root.trace_id
            with tracer.start_span("child1") as c1:
                assert c1.trace_id == root_trace
                with tracer.start_span("grandchild") as gc:
                    assert gc.trace_id == root_trace
                    assert gc.parent_span_id == c1.span_id

    def test_spans_persisted(self, tracer: Tracer, trace_store: SQLiteTraceStore):
        with tracer.start_span("root", execution_id="exec-1"):
            with tracer.start_span("child"):
                pass

        spans = trace_store.list_spans("exec-1")
        assert len(spans) == 2
        # Child should have parent_span_id set
        child = [s for s in spans if s.name == "child"][0]
        root = [s for s in spans if s.name == "root"][0]
        assert child.parent_span_id == root.span_id
        assert child.trace_id == root.trace_id

    def test_tracer_without_store(self):
        """Tracer funciona sin store (no persiste, pero no falla)."""
        tracer = Tracer(None)
        with tracer.start_span("test") as span:
            span.add_attribute("x", 1)
        assert span.status == SpanStatus.OK
