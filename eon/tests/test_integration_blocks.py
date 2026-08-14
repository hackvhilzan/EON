"""
Tests for the 6 integration blocks (Fase 10):
1. Intelligence wiring (PlanScorer, PlanSimulator, AutoReplanner, Memory)
2. CompositeVerifierAdapter
3. ChromaDB optional backend
4. LLM adapters
5. Telemetry/metrics
6. Console REST API
"""

from __future__ import annotations

import json
import ssl
import urllib.request

import pytest

from eon.llm.base import LLM
from eon.telemetry import MetricsRecorder, get_default_recorder

# ─── Block 5: Telemetry ──────────────────────────────────────


class TestTelemetry:
    """Tests for MetricsRecorder."""

    def test_counter_increment(self):
        recorder = MetricsRecorder()
        recorder.increment("executions.total")
        recorder.increment("executions.total", 2)
        assert recorder.get_counter("executions.total") == 3.0

    def test_gauge(self):
        recorder = MetricsRecorder()
        recorder.gauge("plan.score", 0.85)
        assert recorder.get_gauge("plan.score") == 0.85
        recorder.gauge("plan.score", 0.92)
        assert recorder.get_gauge("plan.score") == 0.92

    def test_histogram(self):
        recorder = MetricsRecorder()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            recorder.histogram("latency", v)
        hist = recorder.get_histogram("latency")
        assert hist.count == 5
        assert hist.min == 1.0
        assert hist.max == 5.0
        assert hist.avg == 3.0
        assert 0 <= hist.p50 <= 5.0
        assert 0 <= hist.p95 <= 5.0

    def test_snapshot(self):
        recorder = MetricsRecorder()
        recorder.increment("test.counter")
        recorder.gauge("test.gauge", 42.0)
        recorder.histogram("test.hist", 1.5)
        snap = recorder.snapshot()
        assert "uptime_seconds" in snap
        assert snap["counters"]["test.counter"] == 1.0
        assert snap["gauges"]["test.gauge"] == 42.0
        assert snap["histograms"]["test.hist"]["count"] == 1
        assert snap["histograms"]["test.hist"]["avg"] == 1.5

    def test_reset(self):
        recorder = MetricsRecorder()
        recorder.increment("x")
        recorder.gauge("y", 1.0)
        recorder.reset()
        assert recorder.get_counter("x") == 0.0
        assert recorder.get_gauge("y") == 0.0

    def test_thread_safety(self):
        import threading

        recorder = MetricsRecorder()

        def worker():
            for _ in range(100):
                recorder.increment("thread.counter")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert recorder.get_counter("thread.counter") == 400.0

    def test_default_recorder_singleton(self):
        r1 = get_default_recorder()
        r2 = get_default_recorder()
        assert r1 is r2


# ─── Block 4: LLM Adapters ────────────────────────────────────


class FakeLLM(LLM):
    """Fake LLM for testing — returns canned responses."""

    name = "fake"

    def __init__(self, response: str = "") -> None:
        self._response = response
        self.last_prompt: str = ""

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self._response


class TestLLMJudgeAdapter:
    """Tests for LLMJudgeAdapter."""

    def test_judge_parses_json_response(self):
        from eon.llm.adapters import LLMJudgeAdapter

        response = '{"cumple": true, "confianza": 0.9, "justificacion": "OK"}'
        llm = FakeLLM(response=response)
        adapter = LLMJudgeAdapter(llm)

        cumple, confianza, justificacion = adapter.juzgar("criterio", "resultado")
        assert cumple is True
        assert confianza == 0.9
        assert justificacion == "OK"

    def test_judge_parses_false(self):
        from eon.llm.adapters import LLMJudgeAdapter

        response = '{"cumple": false, "confianza": 0.2, "justificacion": "No cumple"}'
        llm = FakeLLM(response=response)
        adapter = LLMJudgeAdapter(llm)

        cumple, confianza, _ = adapter.juzgar("criterio", "resultado")
        assert cumple is False
        assert confianza == 0.2

    def test_judge_fallback_regex(self):
        from eon.llm.adapters import LLMJudgeAdapter

        response = 'The result {"cumple": true, "confianza": 0.75, "justificacion": "Good"} end'
        llm = FakeLLM(response=response)
        adapter = LLMJudgeAdapter(llm)

        cumple, confianza, _ = adapter.juzgar("criterio", "resultado")
        assert cumple is True
        assert confianza == 0.75

    def test_judge_error_handling(self):
        from eon.llm.adapters import LLMJudgeAdapter

        class CrashingLLM(LLM):
            name = "crash"

            def generate(self, prompt: str) -> str:
                raise RuntimeError("LLM unavailable")

        adapter = LLMJudgeAdapter(CrashingLLM())
        cumple, confianza, justificacion = adapter.juzgar("criterio", "resultado")
        assert cumple is False
        assert confianza == 0.0
        assert "Error" in justificacion

    def test_confianza_clamped(self):
        from eon.llm.adapters import LLMJudgeAdapter

        response = '{"cumple": true, "confianza": 1.5, "justificacion": "x"}'
        adapter = LLMJudgeAdapter(FakeLLM(response=response))
        _, confianza, _ = adapter.juzgar("c", "r")
        assert confianza == 1.0

        response2 = '{"cumple": true, "confianza": -0.5, "justificacion": "x"}'
        adapter2 = LLMJudgeAdapter(FakeLLM(response=response2))
        _, confianza2, _ = adapter2.juzgar("c", "r")
        assert confianza2 == 0.0


class TestDecomposerLLMAdapter:
    """Tests for DecomposerLLMAdapter."""

    def test_decompose_json(self):
        from eon.llm.adapters import DecomposerLLMAdapter

        response = '["step 1", "step 2", "step 3"]'
        adapter = DecomposerLLMAdapter(FakeLLM(response=response))
        result = adapter.decompose("Build a web app")
        assert len(result) == 3
        assert result[0] == "step 1"

    def test_decompose_max_sub(self):
        from eon.llm.adapters import DecomposerLLMAdapter

        response = '["a", "b", "c", "d", "e", "f", "g"]'
        adapter = DecomposerLLMAdapter(FakeLLM(response=response))
        result = adapter.decompose("obj", max_sub=3)
        assert len(result) == 3

    def test_decompose_fallback_lines(self):
        from eon.llm.adapters import DecomposerLLMAdapter

        response = "1. First step\n2. Second step\n3. Third step"
        adapter = DecomposerLLMAdapter(FakeLLM(response=response))
        result = adapter.decompose("obj")
        assert len(result) == 3

    def test_decompose_error(self):
        from eon.llm.adapters import DecomposerLLMAdapter

        class CrashLLM(LLM):
            name = "crash"

            def generate(self, prompt: str) -> str:
                raise RuntimeError("fail")

        adapter = DecomposerLLMAdapter(CrashLLM())
        result = adapter.decompose("obj")
        assert result == []


# ─── Block 3: ChromaDB Backend ───────────────────────────────


class TestChromaBackend:
    """Tests for ChromaDB optional backend."""

    def test_create_backend_memory(self):
        from eon.memory import SemanticMemory

        store = SemanticMemory.create_backend(backend="memory")
        assert store is not None
        assert hasattr(store, "add")
        assert hasattr(store, "search")

    def test_create_backend_chroma_fallback(self):
        """If chromadb not installed, falls back to InMemoryVectorStore."""
        from eon.memory import InMemoryVectorStore, SemanticMemory

        store = SemanticMemory.create_backend(backend="chroma")
        # Should fall back to InMemoryVectorStore if chromadb not installed,
        # or return ChromaVectorStore if it is.
        assert isinstance(store, InMemoryVectorStore)

    def test_chroma_import_error_message(self):
        """ChromaVectorStore raises ImportError with helpful message."""
        try:
            import chromadb  # noqa: F401

            pytest.skip("chromadb is installed, skipping import error test")
        except ImportError:
            pass

        from eon.memory.chroma_backend import ChromaVectorStore

        with pytest.raises(ImportError, match="chromadb"):
            ChromaVectorStore()

    def test_get_chroma_backend(self):
        from eon.memory import get_chroma_backend

        result = get_chroma_backend()
        # None if chromadb not installed, class if it is
        assert result is None or hasattr(result, "__name__")


# ─── ChromaDB Real Persistence Tests ───────────────────────────


def _chroma_available() -> bool:
    """Check if chromadb is installed and importable."""
    try:
        import chromadb  # noqa: F401

        return True
    except ImportError:
        return False


class TestChromaVectorStore:
    """Unit tests for ChromaVectorStore with real ChromaDB."""

    def test_chroma_available(self):
        """If chromadb is installed, ChromaVectorStore should be usable."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

    def test_ephemeral_store_add_search(self, tmp_path):
        """Add and search entries in an ephemeral (in-memory) ChromaDB."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore

        store = ChromaVectorStore(
            collection_name="test_ephemeral",
            persist_path=str(tmp_path / "chroma"),
        )
        try:
            store.add("id-1", "Desplegar API en producción", {"type": "objective"})
            store.add("id-2", "Configurar base de datos", {"type": "objective"})
            store.add("id-3", "Deploy API to production", {"type": "objective"})

            assert store.count == 3

            results = store.search("deploy API", limit=2)
            assert len(results) <= 2
            assert all(isinstance(score, float) for score, _ in results)
            assert all(hasattr(entry, "id") for _, entry in results)
        finally:
            store.destroy()

    def test_persistence_survives_restart(self, tmp_path):
        """Data persists across store instances with the same persist_path."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore

        chroma_path = str(tmp_path / "chroma_persist")

        # First instance: write data
        store1 = ChromaVectorStore(
            collection_name="eon_test_persist",
            persist_path=chroma_path,
        )
        store1.add("persist-1", "Build a REST API with FastAPI", {"tag": "api"})
        store1.add("persist-2", "Deploy to Kubernetes cluster", {"tag": "infra"})
        assert store1.count == 2
        store1.close()

        # Second instance: read back from same path
        store2 = ChromaVectorStore(
            collection_name="eon_test_persist",
            persist_path=chroma_path,
        )
        assert store2.count == 2  # Data survived!

        results = store2.search("create API endpoint", limit=1)
        assert len(results) >= 1
        # The API-related entry should be more relevant
        top_entry = results[0][1]
        assert top_entry.id in ("persist-1", "persist-2")
        store2.destroy()

    def test_upsert_updates_existing(self, tmp_path):
        """Adding with the same ID updates instead of duplicating."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore

        store = ChromaVectorStore(
            collection_name="test_upsert",
            persist_path=str(tmp_path / "chroma_upsert"),
        )
        try:
            store.add("id-1", "Original text", {"version": 1})
            assert store.count == 1

            store.add("id-1", "Updated text", {"version": 2})
            assert store.count == 1  # No duplicate

            results = store.search("Updated", limit=1)
            if results:
                assert results[0][1].metadata.get("version") == 2
        finally:
            store.destroy()

    def test_delete_by_ids(self, tmp_path):
        """Delete entries by their IDs."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore

        store = ChromaVectorStore(
            collection_name="test_delete",
            persist_path=str(tmp_path / "chroma_delete"),
        )
        try:
            store.add("del-1", "First entry", {"n": 1})
            store.add("del-2", "Second entry", {"n": 2})
            store.add("del-3", "Third entry", {"n": 3})
            assert store.count == 3

            store.delete(ids=["del-2"])
            assert store.count == 2
        finally:
            store.destroy()

    def test_clear_resets_collection(self, tmp_path):
        """clear() removes all entries but keeps the collection usable."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore

        store = ChromaVectorStore(
            collection_name="test_clear",
            persist_path=str(tmp_path / "chroma_clear"),
        )
        try:
            store.add("c-1", "Entry one")
            store.add("c-2", "Entry two")
            assert store.count == 2

            store.clear()
            assert store.count == 0

            # Collection still usable after clear
            store.add("c-3", "New entry")
            assert store.count == 1
        finally:
            store.destroy()

    def test_custom_embedding_provider(self, tmp_path):
        """ChromaVectorStore works with a custom EmbeddingProvider."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore
        from eon.memory.semantic import HashingEmbedder

        store = ChromaVectorStore(
            collection_name="test_custom_embed",
            persist_path=str(tmp_path / "chroma_custom"),
            embedding_provider=HashingEmbedder(dim=64),
        )
        try:
            store.add("e-1", "hello world")
            store.add("e-2", "goodbye world")

            results = store.search("hello", limit=1)
            assert len(results) >= 1
        finally:
            store.destroy()

    def test_destroy_removes_directory(self, tmp_path):
        """destroy() removes the persistence directory."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        import os

        from eon.memory.chroma_backend import ChromaVectorStore

        chroma_path = str(tmp_path / "chroma_destroy")
        store = ChromaVectorStore(
            collection_name="test_destroy_dir",
            persist_path=chroma_path,
        )
        store.add("d-1", "To be destroyed")
        assert os.path.exists(chroma_path)

        store.destroy()
        assert not os.path.exists(chroma_path)

    def test_metadata_sanitization(self, tmp_path):
        """Non-primitive metadata values are converted to strings."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore

        store = ChromaVectorStore(
            collection_name="test_meta",
            persist_path=str(tmp_path / "chroma_meta"),
        )
        try:
            store.add(
                "m-1",
                "test",
                {
                    "str_val": "hello",
                    "int_val": 42,
                    "float_val": 3.14,
                    "bool_val": True,
                    "list_val": [1, 2, 3],  # Should become string
                    "none_val": None,  # Should be skipped
                },
            )
            assert store.count == 1

            results = store.search("test", limit=1)
            assert len(results) == 1
            meta = results[0][1].metadata
            assert meta["str_val"] == "hello"
            assert meta["int_val"] == 42
            assert meta["float_val"] == 3.14
            assert meta["bool_val"] is True
            assert meta["list_val"] == "[1, 2, 3]"
            assert "none_val" not in meta
        finally:
            store.destroy()

    def test_close_and_reuse(self, tmp_path):
        """close() cleans references; store can be recreated from same path."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore

        chroma_path = str(tmp_path / "chroma_close")
        store1 = ChromaVectorStore(
            collection_name="test_close",
            persist_path=chroma_path,
        )
        store1.add("cl-1", "Persisted entry")
        store1.close()

        store2 = ChromaVectorStore(
            collection_name="test_close",
            persist_path=chroma_path,
        )
        assert store2.count == 1
        store2.destroy()

    def test_filter_fn_in_search(self, tmp_path):
        """filter_fn is applied to search results."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory.chroma_backend import ChromaVectorStore

        store = ChromaVectorStore(
            collection_name="test_filter",
            persist_path=str(tmp_path / "chroma_filter"),
        )
        try:
            store.add("f-1", "apple fruit", {"category": "food"})
            store.add("f-2", "apple computer", {"category": "tech"})

            results = store.search(
                "apple",
                limit=5,
                filter_fn=lambda entry: entry.metadata.get("category") == "food",
            )
            assert len(results) >= 1
            assert all(e.metadata.get("category") == "food" for _, e in results)
        finally:
            store.destroy()


class TestSemanticMemoryChromaPersistence:
    """Integration tests: SemanticMemory with ChromaDB persistence."""

    def test_semantic_memory_chroma_persistence(self, tmp_path):
        """SemanticMemory with chroma backend persists across instances."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory import SemanticMemory

        chroma_path = str(tmp_path / "chroma_semantic")

        # First instance: store memories
        mem1 = SemanticMemory.create_backend(
            backend="chroma",
            collection_name="eon_semantic_test",
            persist_path=chroma_path,
        )
        sm1 = SemanticMemory(store=mem1)
        sm1.remember("mem-1", "Deploy microservice to Kubernetes", {"tag": "infra"})
        sm1.remember("mem-2", "Write unit tests with pytest", {"tag": "dev"})
        assert sm1.size == 2

        # Second instance: recall from persisted data
        mem2 = SemanticMemory.create_backend(
            backend="chroma",
            collection_name="eon_semantic_test",
            persist_path=chroma_path,
        )
        sm2 = SemanticMemory(store=mem2)
        assert sm2.size == 2  # Data persisted!

        results = sm2.recall("deploy to kubernetes", limit=1)
        assert len(results) >= 1
        # Most relevant result should be the infra entry
        top_score, top_text, top_meta = results[0]
        assert "Kubernetes" in top_text or "microservice" in top_text

        # Cleanup
        mem2.destroy()

    def test_create_backend_chroma_with_custom_embedding(self, tmp_path):
        """create_backend with chroma and custom embedding_provider."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory import SemanticMemory
        from eon.memory.semantic import HashingEmbedder

        store = SemanticMemory.create_backend(
            backend="chroma",
            collection_name="eon_custom_embed",
            persist_path=str(tmp_path / "chroma_custom"),
            embedding_provider=HashingEmbedder(dim=64),
        )
        sm = SemanticMemory(store=store)
        sm.remember("e-1", "test entry")
        results = sm.recall("test", limit=1)
        assert len(results) >= 1
        store.destroy()

    def test_create_backend_chroma_distance_metric(self, tmp_path):
        """create_backend with l2 distance metric."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.memory import SemanticMemory

        store = SemanticMemory.create_backend(
            backend="chroma",
            collection_name="eon_l2",
            persist_path=str(tmp_path / "chroma_l2"),
            distance_metric="l2",
        )
        sm = SemanticMemory(store=store)
        sm.remember("l2-1", "hello world")
        results = sm.recall("hello", limit=1)
        assert len(results) >= 1
        store.destroy()


class TestEnhancedKernelRuntimeChroma:
    """Integration test: EnhancedKernelRuntime with real ChromaDB."""

    def test_runtime_with_chroma_persistence(self, tmp_path):
        """EnhancedKernelRuntime uses ChromaDB for semantic memory with real persistence."""
        if not _chroma_available():
            pytest.skip("chromadb not installed")

        from eon.intelligence import EnhancedKernelRuntime, IntelligenceConfig

        chroma_path = str(tmp_path / "chroma_runtime")
        config = IntelligenceConfig(
            enable_semantic_memory=True,
            semantic_backend="chroma",
            chroma_persist_path=chroma_path,
            chroma_collection_name="eon_runtime_test",
        )
        runtime = EnhancedKernelRuntime(
            root=str(tmp_path / "eon"),
            intelligence_config=config,
        )
        try:
            sem = runtime._hooks._ensure_semantic_memory()
            sem.remember("rt-1", "Build a REST API", {"type": "objective"})
            assert sem.size == 1

            results = sem.recall("create API endpoint", limit=1)
            assert len(results) >= 1
        finally:
            runtime.close()
            # Clean up chroma
            import shutil

            if __import__("os").path.exists(chroma_path):
                shutil.rmtree(chroma_path, ignore_errors=True)


# ─── Block 2: CompositeVerifierAdapter ────────────────────────


class TestCompositeVerifierAdapter:
    """Tests for CompositeVerifierAdapter."""

    def test_adapter_with_composite(self):
        from eon.verification import CompositeVerifier, CompositeVerifierAdapter

        composite = CompositeVerifier()
        adapter = CompositeVerifierAdapter(composite=composite)

        evidence = {"ok": True, "tasks_totales": 2, "tasks_completadas": 2}
        result = adapter.verificar(evidence, "criterio de éxito", 0.7)

        assert hasattr(result, "dictamen")
        assert hasattr(result, "confianza")
        assert hasattr(result, "justificacion")
        assert result.dictamen in ("aprobado", "rechazado")
        assert adapter.last_result is not None

    def test_adapter_fallback_to_basic(self):
        from eon.verification import CompositeVerifierAdapter

        adapter = CompositeVerifierAdapter(composite=None)
        evidence = {"ok": True, "tasks_totales": 1, "tasks_completadas": 1}
        result = adapter.verificar(evidence, "criterio", 0.5)

        assert result.dictamen in ("aprobado", "rechazado")

    def test_adapter_stores_last_result(self):
        from eon.verification import CompositeVerifier, CompositeVerifierAdapter

        composite = CompositeVerifier()
        adapter = CompositeVerifierAdapter(composite=composite)

        assert adapter.last_result is None
        adapter.verificar({"ok": True}, "criterio", 0.7)
        assert adapter.last_result is not None
        assert adapter.last_confidence >= 0.0


# ─── Block 1: Intelligence Wiring ────────────────────────────


class TestIntelligenceConfig:
    """Tests for IntelligenceConfig."""

    def test_defaults_disabled(self):
        from eon.intelligence import IntelligenceConfig

        config = IntelligenceConfig()
        assert not config.enable_plan_scoring
        assert not config.enable_episodic_memory
        assert not config.any_enabled

    def test_any_enabled(self):
        from eon.intelligence import IntelligenceConfig

        config = IntelligenceConfig(enable_plan_scoring=True)
        assert config.any_enabled

    def test_all_features(self):
        from eon.intelligence import IntelligenceConfig

        config = IntelligenceConfig(
            enable_plan_scoring=True,
            enable_plan_simulation=True,
            enable_auto_replanning=True,
            enable_episodic_memory=True,
            enable_semantic_memory=True,
            enable_skill_library=True,
            enable_failure_patterns=True,
            enable_verification_learning=True,
        )
        assert config.any_enabled


class TestIntelligenceHooks:
    """Tests for IntelligenceHooks."""

    def setup_method(self):
        self._cleanup_hooks = []

    def teardown_method(self):
        for hooks in self._cleanup_hooks:
            try:
                hooks.close()
            except Exception:
                pass
        self._cleanup_hooks.clear()

    def _make_hooks(self, **kwargs):
        from eon.intelligence import IntelligenceConfig, IntelligenceHooks

        config = IntelligenceConfig(**kwargs)
        metrics = MetricsRecorder()
        hooks = IntelligenceHooks(config=config, metrics=metrics)
        self._cleanup_hooks.append(hooks)
        return hooks, metrics

    def test_on_plan_created_scoring(self):
        hooks, metrics = self._make_hooks(enable_plan_scoring=True)

        tasks = [{"id": "t1", "capability_id": "fs.write"}]
        result = hooks.on_plan_created("plan1", tasks, "success criteria")

        assert result["score"] is not None
        assert metrics.get_gauge("plan.score") > 0

    def test_on_plan_created_simulation(self):
        hooks, metrics = self._make_hooks(enable_plan_simulation=True)

        tasks = [{"id": "t1", "capability_id": "fs.write"}]
        result = hooks.on_plan_created("plan1", tasks, available_capabilities=["fs.write"])

        assert result["simulation"] is not None
        assert metrics.get_gauge("plan.simulation.confidence") >= 0

    def test_on_plan_created_disabled(self):
        hooks, metrics = self._make_hooks()

        result = hooks.on_plan_created("plan1", [])
        assert result["score"] is None
        assert result["simulation"] is None
        assert result["should_proceed"] is True

    def test_on_verification_rejected(self):
        hooks, metrics = self._make_hooks(enable_auto_replanning=True)

        suggestion = hooks.on_verification_rejected(
            execution_id="exec1",
            failure_reason="timeout",
            attempt_number=1,
        )

        assert suggestion is not None
        assert "should_replan" in suggestion
        assert metrics.get_counter("replan.triggered") == 1.0

    def test_on_verification_rejected_disabled(self):
        hooks, metrics = self._make_hooks()

        result = hooks.on_verification_rejected("exec1", "fail", 1)
        assert result is None

    def test_on_execution_completed_episodic(self):
        hooks, metrics = self._make_hooks(enable_episodic_memory=True)

        hooks.on_execution_completed(
            execution_id="exec1",
            objective_description="test",
            success_criteria="criteria",
            result_cumple=True,
            result_confidence=0.9,
            tasks_total=2,
            tasks_completed=2,
        )

        assert metrics.get_counter("executions.completed") == 1.0
        assert metrics.get_counter("memory.episodic.saved") == 1.0

    def test_on_execution_completed_failed(self):
        hooks, metrics = self._make_hooks(
            enable_episodic_memory=True,
            enable_failure_patterns=True,
        )

        hooks.on_execution_completed(
            execution_id="exec1",
            objective_description="test",
            success_criteria="criteria",
            result_cumple=False,
            result_confidence=0.1,
            tasks_total=2,
            tasks_completed=1,
            tasks_failed=1,
            failed_task_ids=["task1"],
        )

        assert metrics.get_counter("executions.failed") == 1.0
        assert metrics.get_counter("memory.failure.recorded") == 1.0

    def test_on_execution_completed_skill(self):
        hooks, metrics = self._make_hooks(enable_skill_library=True)

        hooks.on_execution_completed(
            execution_id="exec1",
            objective_description="test",
            success_criteria="criteria",
            result_cumple=True,
            result_confidence=0.95,
        )

        assert metrics.get_counter("memory.skill.registered") == 1.0

    def test_on_execution_completed_semantic(self):
        hooks, metrics = self._make_hooks(enable_semantic_memory=True)

        hooks.on_execution_completed(
            execution_id="exec1",
            objective_description="test",
            success_criteria="criteria",
            result_cumple=True,
            result_confidence=0.9,
        )

        assert metrics.get_counter("memory.semantic.saved") == 1.0

    def test_on_execution_completed_verification_learning(self):
        hooks, metrics = self._make_hooks(enable_verification_learning=True)

        hooks.on_execution_completed(
            execution_id="exec1",
            objective_description="test",
            success_criteria="criteria",
            result_cumple=True,
            result_confidence=0.85,
        )

        assert metrics.get_counter("memory.verification.learned") == 1.0

    def test_on_verification_result_metrics(self):
        hooks, metrics = self._make_hooks()

        hooks.on_verification_result(0.92, "aprobado")
        assert metrics.get_gauge("verification.confidence") == 0.92
        assert metrics.get_counter("verification.aprobado") == 1.0

    def test_hooks_close(self):
        hooks, _ = self._make_hooks(enable_episodic_memory=True)
        hooks.close()  # Should not raise


class TestEnhancedKernelRuntime:
    """Tests for EnhancedKernelRuntime."""

    def test_create_without_config(self, tmp_path):
        from eon.intelligence import EnhancedKernelRuntime

        runtime = EnhancedKernelRuntime(root=str(tmp_path / "eon"))
        try:
            assert runtime.metrics is not None
            assert runtime.hooks is not None
        finally:
            runtime.close()

    def test_create_with_intelligence(self, tmp_path):
        from eon.intelligence import EnhancedKernelRuntime, IntelligenceConfig

        config = IntelligenceConfig(
            enable_plan_scoring=True,
            enable_plan_simulation=True,
            enable_episodic_memory=True,
        )
        runtime = EnhancedKernelRuntime(
            root=str(tmp_path / "eon"),
            intelligence_config=config,
        )
        try:
            assert runtime.metrics is not None
            # Score a plan
            result = runtime.score_plan(
                "plan1",
                [{"id": "t1", "capability_id": "fs.write"}],
                "success criteria",
            )
            assert result["score"] is not None
            assert result["simulation"] is not None
        finally:
            runtime.close()

    def test_run_records_metrics(self, tmp_path):
        from eon.intelligence import EnhancedKernelRuntime, IntelligenceConfig
        from eon.planner.task import Task

        config = IntelligenceConfig(enable_episodic_memory=True)
        tasks = [Task(capability_id="fs.write", id="t1", depende_de=(), parametros={})]
        runtime = EnhancedKernelRuntime(
            root=str(tmp_path / "eon"),
            tasks=tasks,
            intelligence_config=config,
        )
        try:
            result = runtime.run("test objective", "criterio")
            assert result.execution_id
            snap = runtime.metrics.snapshot()
            assert snap["counters"].get("executions.completed", 0) >= 1
            assert snap["counters"].get("memory.episodic.saved", 0) >= 1
        finally:
            runtime.close()


# ─── Block 6: Console REST API ────────────────────────────────


class TestConsoleServer:
    """Tests for ConsoleServer."""

    def _make_runtime(self, tmp_path):
        from eon.intelligence import EnhancedKernelRuntime, IntelligenceConfig

        runtime = EnhancedKernelRuntime(
            root=str(tmp_path / "eon"),
            intelligence_config=IntelligenceConfig(enable_console=True, console_port=0),
        )
        # Port 0 = OS picks a free port, but we need to know which one.
        # Re-create with a specific port.
        runtime.close()
        return None  # We'll use a simpler approach below

    def test_health_endpoint(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(runtime=runtime, port=18099)
        try:
            server.start()
            assert server.is_running

            resp = urllib.request.urlopen("http://127.0.0.1:18099/health", timeout=5)
            data = json.loads(resp.read())
            assert data["status"] == "ok"
        finally:
            server.stop()
            runtime.close()

    def test_metrics_endpoint(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime
        from eon.telemetry import MetricsRecorder

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        metrics = MetricsRecorder()
        metrics.increment("test.counter", 5)
        server = ConsoleServer(runtime=runtime, port=18098, metrics=metrics)
        try:
            server.start()
            resp = urllib.request.urlopen("http://127.0.0.1:18098/metrics", timeout=5)
            data = json.loads(resp.read())
            assert "counters" in data
            assert data["counters"]["test.counter"] == 5.0
        finally:
            server.stop()
            runtime.close()

    def test_executions_endpoint(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(runtime=runtime, port=18097)
        try:
            server.start()
            resp = urllib.request.urlopen("http://127.0.0.1:18097/executions", timeout=5)
            data = json.loads(resp.read())
            assert "executions" in data
            assert isinstance(data["executions"], list)
        finally:
            server.stop()
            runtime.close()

    def test_404_endpoint(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(runtime=runtime, port=18096)
        try:
            server.start()
            try:
                resp = urllib.request.urlopen("http://127.0.0.1:18096/nonexistent", timeout=5)
                resp.close()
                pytest.fail("Should have raised HTTPError")
            except urllib.error.HTTPError as e:
                assert e.code == 404
                # Read and close the error response to avoid resource leaks
                e.read()
                e.close()
        finally:
            server.stop()
            runtime.close()

    def test_server_url(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(runtime=runtime, port=18095)
        try:
            assert server.url == "http://127.0.0.1:18095"
            server.start()
            assert server.is_running
        finally:
            server.stop()
            runtime.close()

    def test_start_stop_idempotent(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(runtime=runtime, port=18094)
        try:
            server.start()
            server.start()  # no-op
            assert server.is_running
            server.stop()
            server.stop()  # no-op
            assert not server.is_running
        finally:
            runtime.close()


class TestConsoleAuth:
    """Tests for Bearer token authentication."""

    def test_health_public_without_auth(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18093,
            auth_token="secret-token-123",
        )
        try:
            server.start()
            # /health no requiere auth
            resp = urllib.request.urlopen("http://127.0.0.1:18093/health", timeout=5)
            data = json.loads(resp.read())
            assert data["status"] == "ok"
        finally:
            server.stop()
            runtime.close()

    def test_protected_without_token_401(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18092,
            auth_token="secret-token-123",
        )
        try:
            server.start()
            assert server.is_auth_enabled
            try:
                resp = urllib.request.urlopen("http://127.0.0.1:18092/executions", timeout=5)
                resp.close()
                pytest.fail("Should have raised HTTPError 401")
            except urllib.error.HTTPError as e:
                assert e.code == 401
                e.read()
                e.close()
        finally:
            server.stop()
            runtime.close()

    def test_protected_with_valid_token(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18091,
            auth_token="secret-token-123",
        )
        try:
            server.start()
            req = urllib.request.Request(
                "http://127.0.0.1:18091/executions",
                headers={"Authorization": "Bearer secret-token-123"},
            )
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            assert "executions" in data
        finally:
            server.stop()
            runtime.close()

    def test_protected_with_invalid_token_401(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18090,
            auth_token="secret-token-123",
        )
        try:
            server.start()
            req = urllib.request.Request(
                "http://127.0.0.1:18090/executions",
                headers={"Authorization": "Bearer wrong-token"},
            )
            try:
                resp = urllib.request.urlopen(req, timeout=5)
                resp.close()
                pytest.fail("Should have raised HTTPError 401")
            except urllib.error.HTTPError as e:
                assert e.code == 401
                e.read()
                e.close()
        finally:
            server.stop()
            runtime.close()

    def test_protected_with_malformed_auth_401(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18089,
            auth_token="secret-token-123",
        )
        try:
            server.start()
            # No Bearer prefix
            req = urllib.request.Request(
                "http://127.0.0.1:18089/executions",
                headers={"Authorization": "secret-token-123"},
            )
            try:
                urllib.request.urlopen(req, timeout=5)
                pytest.fail("Should have raised HTTPError 401")
            except urllib.error.HTTPError as e:
                assert e.code == 401
                e.read()
                e.close()
        finally:
            server.stop()
            runtime.close()

    def test_multiple_tokens(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18088,
            auth_tokens=["token-a", "token-b"],
        )
        try:
            server.start()
            # Token A funciona
            req_a = urllib.request.Request(
                "http://127.0.0.1:18088/executions",
                headers={"Authorization": "Bearer token-a"},
            )
            resp_a = urllib.request.urlopen(req_a, timeout=5)
            assert resp_a.status == 200
            resp_a.read()
            resp_a.close()

            # Token B funciona
            req_b = urllib.request.Request(
                "http://127.0.0.1:18088/executions",
                headers={"Authorization": "Bearer token-b"},
            )
            resp_b = urllib.request.urlopen(req_b, timeout=5)
            assert resp_b.status == 200
            resp_b.read()
            resp_b.close()
        finally:
            server.stop()
            runtime.close()

    def test_generate_token(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(runtime=runtime, port=18087)
        try:
            token = server.generate_token()
            assert len(token) == 64  # 32 bytes hex
            assert all(c in "0123456789abcdef" for c in token)
        finally:
            server.stop()
            runtime.close()

    def test_auth_disabled_by_default(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(runtime=runtime, port=18086)
        try:
            assert not server.is_auth_enabled
        finally:
            server.stop()
            runtime.close()


class TestConsoleTLS:
    """Tests for TLS (HTTPS) support."""

    def test_tls_not_configured_by_default(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(runtime=runtime, port=18085)
        try:
            assert not server.is_tls
            assert server.url.startswith("http://")
        finally:
            runtime.close()

    def test_tls_auto_generate_self_signed(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        certfile = str(tmp_path / "cert.pem")
        keyfile = str(tmp_path / "key.pem")

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18084,
            tls_certfile=certfile,
            tls_keyfile=keyfile,
            tls_auto_generate=True,
        )
        try:
            # Los certificados se generan al hacer start()
            server.start()
            assert server.is_tls
            assert server.url.startswith("https://")
            # Verificar que los archivos existen
            import os

            assert os.path.exists(certfile)
            assert os.path.exists(keyfile)
        finally:
            server.stop()
            runtime.close()

    def test_tls_https_request_with_insecure_context(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        certfile = str(tmp_path / "cert.pem")
        keyfile = str(tmp_path / "key.pem")

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18083,
            tls_certfile=certfile,
            tls_keyfile=keyfile,
            tls_auto_generate=True,
        )
        try:
            server.start()
            if not server.is_tls:
                pytest.skip("TLS not available (openssl/cryptography missing)")

            # Crear contexto SSL que no verifica (cert self-signed)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            resp = urllib.request.urlopen(
                "https://127.0.0.1:18083/health",
                timeout=5,
                context=ctx,
            )
            data = json.loads(resp.read())
            assert data["status"] == "ok"
        finally:
            server.stop()
            runtime.close()

    def test_tls_with_auth_combined(self, tmp_path):
        from eon.console import ConsoleServer
        from eon.runtime import KernelRuntime

        certfile = str(tmp_path / "cert.pem")
        keyfile = str(tmp_path / "key.pem")

        runtime = KernelRuntime(root=str(tmp_path / "eon"))
        server = ConsoleServer(
            runtime=runtime,
            port=18082,
            auth_token="tls-secret-token",
            tls_certfile=certfile,
            tls_keyfile=keyfile,
            tls_auto_generate=True,
        )
        try:
            server.start()
            if not server.is_tls:
                pytest.skip("TLS not available")

            assert server.is_tls
            assert server.is_auth_enabled

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            # /health funciona sin token
            resp = urllib.request.urlopen(
                "https://127.0.0.1:18082/health",
                timeout=5,
                context=ctx,
            )
            assert json.loads(resp.read())["status"] == "ok"

            # /executions sin token → 401
            try:
                urllib.request.urlopen(
                    "https://127.0.0.1:18082/executions",
                    timeout=5,
                    context=ctx,
                )
                pytest.fail("Should 401")
            except urllib.error.HTTPError as e:
                assert e.code == 401
                e.read()
                e.close()

            # /executions con token → 200
            req = urllib.request.Request(
                "https://127.0.0.1:18082/executions",
                headers={"Authorization": "Bearer tls-secret-token"},
            )
            resp2 = urllib.request.urlopen(req, timeout=5, context=ctx)
            assert resp2.status == 200
            resp2.read()
            resp2.close()
        finally:
            server.stop()
            runtime.close()


class TestAuthProvider:
    """Unit tests for AuthProvider."""

    def test_disabled_when_no_tokens(self):
        from eon.console import AuthProvider

        auth = AuthProvider()
        assert not auth.enabled
        assert auth.validate("anything") is True
        assert auth.validate(None) is True

    def test_single_token(self):
        from eon.console import AuthProvider

        auth = AuthProvider(auth_token="my-token")
        assert auth.enabled
        assert auth.validate("my-token") is True
        assert auth.validate("wrong") is False
        assert auth.validate(None) is False

    def test_multiple_tokens(self):
        from eon.console import AuthProvider

        auth = AuthProvider(auth_tokens=["a", "b", "c"])
        assert auth.enabled
        assert auth.validate("a") is True
        assert auth.validate("b") is True
        assert auth.validate("c") is True
        assert auth.validate("d") is False

    def test_timing_safe_comparison(self):
        from eon.console import AuthProvider

        auth = AuthProvider(auth_token="x" * 32)
        # No debe lanzar excepción
        assert auth.validate("x" * 32) is True
        assert auth.validate("y" * 32) is False

    def test_generate_token_format(self):
        from eon.console import AuthProvider

        auth = AuthProvider()
        token = auth.generate_token()
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)
        # Dos llamadas generan tokens diferentes
        token2 = auth.generate_token()
        assert token != token2


class TestTLSCertGenerator:
    """Tests for TLSCertGenerator."""

    def test_generate_self_signed(self, tmp_path):
        import os

        from eon.console import TLSCertGenerator

        certfile = str(tmp_path / "test_cert.pem")
        keyfile = str(tmp_path / "test_key.pem")

        result = TLSCertGenerator.generate_self_signed(
            certfile=certfile,
            keyfile=keyfile,
            common_name="test.local",
        )

        # openssl o cryptography deben estar disponibles en el sandbox
        if not result:
            pytest.skip("Neither openssl nor cryptography available")

        assert os.path.exists(certfile)
        assert os.path.exists(keyfile)

        # Verificar que el certificado es PEM válido
        with open(certfile) as f:
            content = f.read()
        assert "BEGIN CERTIFICATE" in content
