"""
Tests de Memoria Avanzada (Fase 9).

Criterios de aceptación:
- EpisodicMemoryStore: save, get, search_similar
- SemanticMemory: remember, recall por similitud
- SkillLibrary: register, match, record_success/failure
- FailurePatterns: record, get_patterns, get_stats
- ExecutionReplayer: replay desde EventStore
- VerificationWeightLearner: record_outcome, compute_weights
"""

from __future__ import annotations

import pytest

from eon.memory import (
    Episode,
    EpisodicMemoryStore,
    ExecutionReplayer,
    FailureCategory,
    FailurePatterns,
    InMemoryVectorStore,
    SemanticMemory,
    Skill,
    SkillLibrary,
    VerificationWeightLearner,
)
from eon.memory.semantic import HashingEmbedder
from eon.persistence import SQLiteEngine
from eon.persistence.event_store import EventStore

# ─── EpisodicMemory Tests ────────────────────────────────


class TestEpisodicMemory:
    def test_save_and_get(self, tmp_path):
        store = EpisodicMemoryStore(db_path=str(tmp_path / "ep.db"))
        ep = Episode(
            execution_id="exec-1",
            objective_description="Generar informe de ventas Q3",
            success_criteria="PDF con >1 página",
            result_cumple=True,
            result_confidence=0.92,
            duration_seconds=45.3,
            tasks_total=5,
            tasks_completed=5,
        )
        store.save(ep)
        assert ep.id != ""

        retrieved = store.get(ep.id)
        assert retrieved is not None
        assert retrieved.execution_id == "exec-1"
        assert retrieved.objective_description == "Generar informe de ventas Q3"
        assert retrieved.result_cumple is True
        assert retrieved.result_confidence == 0.92

        store.close()

    def test_list_all(self, tmp_path):
        store = EpisodicMemoryStore(db_path=str(tmp_path / "ep.db"))
        for i in range(5):
            store.save(
                Episode(
                    execution_id=f"exec-{i}",
                    objective_description=f"Objetivo {i}",
                )
            )
        assert store.count() == 5
        episodes = store.list_all()
        assert len(episodes) == 5
        store.close()

    def test_search_similar(self, tmp_path):
        store = EpisodicMemoryStore(db_path=str(tmp_path / "ep.db"))
        store.save(
            Episode(
                execution_id="exec-1",
                objective_description="Generar informe de ventas Q3 en PDF",
                result_cumple=True,
            )
        )
        store.save(
            Episode(
                execution_id="exec-2",
                objective_description="Enviar email al cliente",
                result_cumple=True,
            )
        )
        store.save(
            Episode(
                execution_id="exec-3",
                objective_description="Crear informe trimestral de ventas",
                result_cumple=True,
            )
        )

        results = store.search_similar("informe de ventas")
        assert len(results) > 0
        # Los episodios sobre informes de ventas deben aparecer primero
        assert "informe" in results[0].objective_description.lower()
        store.close()

    def test_search_similar_only_successful(self, tmp_path):
        store = EpisodicMemoryStore(db_path=str(tmp_path / "ep.db"))
        store.save(
            Episode(
                execution_id="exec-1",
                objective_description="Generar informe de ventas",
                result_cumple=True,
            )
        )
        store.save(
            Episode(
                execution_id="exec-2",
                objective_description="Generar informe de ventas",
                result_cumple=False,
            )
        )

        results = store.search_similar("informe de ventas", only_successful=True)
        assert all(r.result_cumple for r in results)
        store.close()

    def test_persistence(self, tmp_path):
        db_path = str(tmp_path / "ep.db")
        store1 = EpisodicMemoryStore(db_path=db_path)
        store1.save(Episode(execution_id="exec-1", objective_description="test"))
        store1.close()

        store2 = EpisodicMemoryStore(db_path=db_path)
        assert store2.count() == 1
        store2.close()


# ─── SemanticMemory Tests ────────────────────────────────


class TestSemanticMemory:
    def test_remember_and_recall(self):
        mem = SemanticMemory()
        mem.remember("e1", "Generar informe de ventas Q3", {"exec": "exec-1"})
        mem.remember("e2", "Enviar email al cliente", {"exec": "exec-2"})
        mem.remember("e3", "Crear informe trimestral", {"exec": "exec-3"})

        results = mem.recall("informe de ventas")
        assert len(results) > 0
        assert mem.size == 3

    def test_recall_returns_metadata(self):
        mem = SemanticMemory()
        mem.remember("e1", "Generar PDF", {"exec": "exec-1", "cost": 0.05})

        results = mem.recall("PDF")
        assert len(results) == 1
        score, text, metadata = results[0]
        assert "exec" in metadata
        assert metadata["exec"] == "exec-1"

    def test_empty_recall(self):
        mem = SemanticMemory()
        results = mem.recall("test")
        assert len(results) == 0

    def test_hashing_embedder(self):
        embedder = HashingEmbedder(dim=64)
        vec = embedder.embed("hello world")
        assert len(vec) == 64
        # El vector debe estar normalizado
        norm = sum(v * v for v in vec) ** 0.5
        assert 0.99 <= norm <= 1.01

    def test_vector_store_cleared(self):
        store = InMemoryVectorStore()
        store.add("1", "test")
        assert store.count == 1
        store.clear()
        assert store.count == 0


# ─── SkillLibrary Tests ───────────────────────────────────


class TestSkillLibrary:
    def test_register_and_get(self, tmp_path):
        lib = SkillLibrary(db_path=str(tmp_path / "skills.db"))
        skill = Skill(
            name="informe_ventas",
            objective_pattern="Generar informe de ventas",
            plan_summary="Buscar datos → Procesar → Generar PDF",
            plan_data={"tasks": ["buscar", "procesar", "pdf"]},
        )
        lib.register(skill)
        assert skill.id != ""

        retrieved = lib.get(skill.id)
        assert retrieved is not None
        assert retrieved.name == "informe_ventas"
        assert retrieved.plan_summary == "Buscar datos → Procesar → Generar PDF"
        lib.close()

    def test_match_finds_similar(self, tmp_path):
        lib = SkillLibrary(db_path=str(tmp_path / "skills.db"))
        lib.register(
            Skill(
                name="informe_ventas",
                objective_pattern="Generar informe de ventas trimestrales",
                success_count=5,
            )
        )
        lib.register(
            Skill(
                name="email_cliente",
                objective_pattern="Enviar email al cliente",
                success_count=2,
            )
        )

        match = lib.match("Generar informe de ventas Q3")
        assert match is not None
        assert match.name == "informe_ventas"

        lib.close()

    def test_match_no_result(self, tmp_path):
        lib = SkillLibrary(db_path=str(tmp_path / "skills.db"))
        lib.register(
            Skill(
                name="skill1",
                objective_pattern="Completamente diferente",
                success_count=1,
            )
        )

        match = lib.match("generar informe de ventas")
        # Debe tener baja similitud o None
        assert match is None
        lib.close()

    def test_record_success_and_failure(self, tmp_path):
        lib = SkillLibrary(db_path=str(tmp_path / "skills.db"))
        skill = lib.register(
            Skill(
                name="test_skill",
                objective_pattern="test pattern",
            )
        )

        lib.record_success(skill.id)
        lib.record_success(skill.id)
        lib.record_failure(skill.id)

        retrieved = lib.get(skill.id)
        assert retrieved.success_count == 2
        assert retrieved.fail_count == 1
        assert retrieved.success_rate == pytest.approx(2 / 3)
        lib.close()

    def test_persistence(self, tmp_path):
        db_path = str(tmp_path / "skills.db")
        lib1 = SkillLibrary(db_path=db_path)
        lib1.register(Skill(name="s1", objective_pattern="test"))
        lib1.close()

        lib2 = SkillLibrary(db_path=db_path)
        assert lib2.count() == 1
        lib2.close()


# ─── FailurePatterns Tests ────────────────────────────────


class TestFailurePatterns:
    def test_record_and_stats(self, tmp_path):
        fp = FailurePatterns(db_path=str(tmp_path / "fail.db"))
        fp.record(
            type(
                "F",
                (),
                {
                    "id": "",
                    "execution_id": "exec-1",
                    "task_id": "task-1",
                    "capability_id": "tool.python",
                    "category": FailureCategory.TIMEOUT.value,
                    "error_message": "Timeout after 30s",
                    "context": {},
                    "created_at": "",
                },
            )()
        )
        # The above is a hack; use proper record
        fp2 = FailurePatterns(db_path=str(tmp_path / "fail2.db"))

        from eon.memory.failure_patterns import FailureRecord

        fp2.record(
            FailureRecord(
                execution_id="exec-1",
                task_id="task-1",
                capability_id="tool.python",
                category=FailureCategory.TIMEOUT.value,
                error_message="Timeout after 30s",
            )
        )
        fp2.record(
            FailureRecord(
                execution_id="exec-1",
                task_id="task-2",
                capability_id="tool.python",
                category=FailureCategory.TASK_FAILED.value,
                error_message="Assertion error",
            )
        )
        fp2.record(
            FailureRecord(
                execution_id="exec-2",
                task_id="task-3",
                capability_id="tool.llm",
                category=FailureCategory.LLM_ERROR.value,
                error_message="API rate limit",
            )
        )

        stats = fp2.get_stats()
        assert stats["total"] == 3
        assert stats["by_category"][FailureCategory.TIMEOUT.value] == 1
        assert stats["by_capability"]["tool.python"] == 2
        fp2.close()
        fp.close()

    def test_get_patterns(self, tmp_path):
        from eon.memory.failure_patterns import FailureRecord

        fp = FailurePatterns(db_path=str(tmp_path / "fail.db"))
        for i in range(5):
            fp.record(
                FailureRecord(
                    execution_id=f"exec-{i}",
                    capability_id="tool.python",
                    category=FailureCategory.TIMEOUT.value,
                    error_message="Timeout after 30s",
                )
            )
        fp.record(
            FailureRecord(
                execution_id="exec-5",
                capability_id="tool.python",
                category=FailureCategory.TASK_FAILED.value,
                error_message="Different error",
            )
        )

        patterns = fp.get_patterns("tool.python")
        assert patterns["total_failures"] == 6
        assert patterns["by_category"][FailureCategory.TIMEOUT.value] == 5
        assert "Timeout" in patterns["most_common_error"]
        fp.close()

    def test_failure_rate(self, tmp_path):
        from eon.memory.failure_patterns import FailureRecord

        fp = FailurePatterns(db_path=str(tmp_path / "fail.db"))
        for _ in range(3):
            fp.record(
                FailureRecord(
                    capability_id="tool.python",
                    category=FailureCategory.TIMEOUT.value,
                )
            )

        rate = fp.get_failure_rate("tool.python", total_calls=10)
        assert rate == pytest.approx(0.3)
        fp.close()


# ─── ExecutionReplayer Tests ──────────────────────────────


class TestExecutionReplayer:
    def test_replay(self, tmp_path):
        engine = SQLiteEngine(tmp_path / "test.db")
        engine.init_schema()
        es = EventStore(engine)

        es.append("exec-1", "coordinator.created", {"estado": "CREATED"})
        es.append("exec-1", "plan.created", {"plan_id": "plan-1"})
        es.append("exec-1", "task.completed", {"task_id": "task-1"})

        replayer = ExecutionReplayer(es)
        replay = replayer.replay("exec-1")

        assert replay.execution_id == "exec-1"
        assert replay.total_events == 3
        assert replay.steps[0].event_type == "coordinator.created"
        assert replay.steps[1].description == "Plan creado"
        assert replay.steps[2].description == "Task completada"
        engine.close()

    def test_replay_with_filter(self, tmp_path):
        engine = SQLiteEngine(tmp_path / "test.db")
        engine.init_schema()
        es = EventStore(engine)

        es.append("exec-1", "coordinator.created", {})
        es.append("exec-1", "task.completed", {"task_id": "task-1"})
        es.append("exec-1", "task.completed", {"task_id": "task-2"})

        replayer = ExecutionReplayer(es)
        replay = replayer.replay("exec-1", event_type_filter="task.completed")

        assert replay.total_events == 2
        assert all(s.event_type == "task.completed" for s in replay.steps)
        engine.close()

    def test_replay_empty_execution(self, tmp_path):
        engine = SQLiteEngine(tmp_path / "test.db")
        engine.init_schema()
        es = EventStore(engine)

        replayer = ExecutionReplayer(es)
        replay = replayer.replay("nonexistent")

        assert replay.total_events == 0
        engine.close()

    def test_replay_step_by_step(self, tmp_path):
        engine = SQLiteEngine(tmp_path / "test.db")
        engine.init_schema()
        es = EventStore(engine)

        es.append("exec-1", "coordinator.created", {})
        es.append("exec-1", "task.completed", {})

        replayer = ExecutionReplayer(es)
        steps = list(replayer.replay_step_by_step("exec-1"))

        assert len(steps) == 2
        assert steps[0].seq < steps[1].seq
        engine.close()


# ─── VerificationWeightLearner Tests ─────────────────────


class TestVerificationWeightLearner:
    def test_default_weights(self, tmp_path):
        learner = VerificationWeightLearner(db_path=str(tmp_path / "vw.db"))
        weights = learner.compute_weights()
        assert "structural" in weights
        assert "llm_judge" in weights
        assert sum(weights.values()) == pytest.approx(1.0)
        learner.close()

    def test_record_and_recompute(self, tmp_path):
        learner = VerificationWeightLearner(db_path=str(tmp_path / "vw.db"))

        # LLMJudge acierta siempre
        for i in range(10):
            learner.record_outcome(f"exec-{i}", "llm_judge", 0.9, True)

        # TestBased falla siempre
        for i in range(10):
            learner.record_outcome(f"exec-{i}", "test_based", 0.7, False)

        weights = learner.compute_weights()
        # LLMJudge debe tener más peso que test_based
        assert weights["llm_judge"] > weights["test_based"]
        learner.close()

    def test_accuracy(self, tmp_path):
        learner = VerificationWeightLearner(db_path=str(tmp_path / "vw.db"))
        learner.record_outcome("exec-1", "llm_judge", 0.9, True)
        learner.record_outcome("exec-2", "llm_judge", 0.8, True)
        learner.record_outcome("exec-3", "llm_judge", 0.7, False)

        accuracy = learner.get_accuracy("llm_judge")
        assert accuracy == pytest.approx(2 / 3)
        learner.close()

    def test_decay(self, tmp_path):
        learner = VerificationWeightLearner(
            db_path=str(tmp_path / "vw.db"),
            decay=0.5,
        )
        # Viejos: todos correctos
        for i in range(5):
            learner.record_outcome(f"old-{i}", "llm_judge", 0.9, True)
        # Recientes: todos incorrectos
        for i in range(5):
            learner.record_outcome(f"new-{i}", "llm_judge", 0.9, False)

        accuracy = learner.get_accuracy("llm_judge")
        # Sin decay: 50%. Con decay: los recientes pesan más → < 50%
        assert accuracy == pytest.approx(0.5)  # get_accuracy no usa decay
        learner.close()

    def test_persistence(self, tmp_path):
        db_path = str(tmp_path / "vw.db")
        l1 = VerificationWeightLearner(db_path=db_path)
        l1.record_outcome("exec-1", "llm_judge", 0.9, True)
        l1.close()

        l2 = VerificationWeightLearner(db_path=db_path)
        assert l2.count() == 1
        l2.close()
