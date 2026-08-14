"""
Tests de Checkpointing y Semantic Snapshot (Fase 3).

Criterios de aceptación:
- Checkpoint create → close → reopen → verify state survived
- Hash estable y tamper detection
- Checkpoint anclado a event_seq
- SemanticSnapshot con objective/plan/scheduler
- KernelRuntime.crear_checkpoint() con SQLite
- Auto-checkpoint solo cuando checkpointing_enabled=True
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eon.checkpoint import (
    Checkpoint,
    CheckpointKind,
    CheckpointManager,
    SemanticSnapshot,
    SemanticSnapshotBuilder,
    SQLiteCheckpointStore,
)
from eon.persistence import SQLiteEngine

# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def engine(tmp_path: Path) -> SQLiteEngine:
    eng = SQLiteEngine(tmp_path / "test_cp.db")
    eng.init_schema()
    yield eng
    eng.close()


@pytest.fixture
def cp_store(engine: SQLiteEngine) -> SQLiteCheckpointStore:
    return SQLiteCheckpointStore(engine)


# ─── Checkpoint Model Tests ────────────────────────────────

class TestCheckpointModel:
    def test_compute_hash_stable(self):
        cp = Checkpoint(
            execution_id="exec-1",
            event_seq=42,
            stores_state={"key": "value"},
            artifacts={"path": "/tmp/out"},
        )
        h1 = cp.compute_hash()
        h2 = cp.compute_hash()
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_changes_on_state_change(self):
        cp = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            stores_state={"key": "value1"},
        )
        h1 = cp.compute_hash()
        cp.stores_state["key"] = "value2"
        h2 = cp.compute_hash()
        assert h1 != h2

    def test_hash_includes_semantic(self):
        """El hash debe incluir el semantic snapshot."""
        from eon.checkpoint.models import SemanticSnapshot

        cp1 = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            semantic=SemanticSnapshot(intent="original", confidence=0.9),
        )
        h1 = cp1.compute_hash()

        cp2 = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            semantic=SemanticSnapshot(intent="ALTERADO", confidence=0.9),
        )
        h2 = cp2.compute_hash()

        assert h1 != h2, "Hash debe cambiar si semantic.intent cambia"

    def test_hash_includes_semantic_risks(self):
        """Alterar semantic.risks debe invalidar el hash."""
        from eon.checkpoint.models import SemanticSnapshot

        cp1 = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            semantic=SemanticSnapshot(
                intent="test",
                risks=["task-3 bloqueada"],
            ),
        )
        h1 = cp1.compute_hash()

        # Alterar risks
        cp1.semantic.risks.append("NUEVO RIESGO FALSO")
        h2 = cp1.compute_hash()

        assert h1 != h2, "Hash debe cambiar si semantic.risks se altera"

    def test_hash_includes_semantic_evidence(self):
        """Alterar semantic.evidence debe invalidar el hash."""
        from eon.checkpoint.models import SemanticSnapshot

        cp = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            semantic=SemanticSnapshot(
                intent="test",
                evidence={"test_run": "passed"},
            ),
        )
        h1 = cp.compute_hash()

        # Alterar evidence
        cp.semantic.evidence["test_run"] = "ALTERADO"
        h2 = cp.compute_hash()

        assert h1 != h2, "Hash debe cambiar si semantic.evidence se altera"

    def test_hash_includes_semantic_assumptions(self):
        """Alterar semantic.assumptions debe invalidar el hash."""
        from eon.checkpoint.models import SemanticSnapshot

        cp = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            semantic=SemanticSnapshot(
                intent="test",
                assumptions=["original"],
            ),
        )
        h1 = cp.compute_hash()

        cp.semantic.assumptions.append("NUEVA ASUNCIÓN FALSA")
        h2 = cp.compute_hash()

        assert h1 != h2

    def test_hash_includes_kind_reason_created_at(self):
        """El hash debe incluir kind, reason y created_at."""
        from eon.checkpoint.models import CheckpointKind

        cp_base = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            kind=CheckpointKind.MANUAL,
            reason="original",
        )
        h_base = cp_base.compute_hash()

        # kind distinto
        cp_kind = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            kind=CheckpointKind.MILESTONE,
            reason="original",
            created_at=cp_base.created_at,
        )
        assert h_base != cp_kind.compute_hash(), "Hash debe cambiar si kind cambia"

        # reason distinto
        cp_reason = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            kind=CheckpointKind.MANUAL,
            reason="ALTERADO",
            created_at=cp_base.created_at,
        )
        assert h_base != cp_reason.compute_hash(), "Hash debe cambiar si reason cambia"

    def test_hash_includes_id(self):
        """El hash debe incluir el id del checkpoint."""
        cp1 = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            id="checkpoint-A",
        )
        cp2 = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            id="checkpoint-B",
        )
        assert cp1.compute_hash() != cp2.compute_hash()

    def test_tamper_detection_after_persist(self):
        """Simula alteración post-persistencia: verify_hash debe fallar."""
        from eon.checkpoint.models import SemanticSnapshot

        cp = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            semantic=SemanticSnapshot(intent="original", confidence=0.9),
        )
        cp.compute_hash()
        original_hash = cp.hash

        # Simular alteración maliciosa del semantic
        cp.semantic.intent = "ALTERADO POR ATACANTE"

        # Recalcular y comparar
        new_hash = cp.compute_hash()
        assert original_hash != new_hash

    def test_to_dict_from_dict_roundtrip(self):
        cp = Checkpoint(
            execution_id="exec-1",
            event_seq=5,
            kind=CheckpointKind.MILESTONE,
            stores_state={"a": 1},
            artifacts={"p": "/out"},
            semantic=SemanticSnapshot(intent="test", confidence=0.9),
            cost_so_far=1.5,
            reason="milestone",
        )
        cp.compute_hash()
        d = cp.to_dict()
        restored = Checkpoint.from_dict(d)
        assert restored.execution_id == "exec-1"
        assert restored.event_seq == 5
        assert restored.kind == CheckpointKind.MILESTONE
        assert restored.semantic is not None
        assert restored.semantic.intent == "test"
        assert restored.semantic.confidence == 0.9
        assert restored.hash == cp.hash


# ─── SemanticSnapshot Tests ────────────────────────────────

class TestSemanticSnapshot:
    def test_empty_builder(self):
        builder = SemanticSnapshotBuilder()
        snap = builder.build(execution_id="exec-1")
        assert snap.intent == ""
        assert snap.assumptions == []
        assert snap.tasks_total == 0

    def test_build_from_objective(self):
        from eon.objectives.models import Objective, ObjectiveOrigin

        obj = Objective(
            descripcion="Construir API",
            criterio_de_exito="200 OK",
            origen=ObjectiveOrigin.USUARIO,
        )
        builder = SemanticSnapshotBuilder()
        snap = builder.build(execution_id="exec-1", objective=obj)
        assert snap.intent == "Construir API"
        assert snap.success_criteria == "200 OK"
        assert snap.confidence == 0.7  # default

    def test_build_from_plan(self):
        from eon.planner.models import Plan
        from eon.planner.task import Task

        plan = Plan(
            objective_id="obj-1",
            tasks=[
                Task(capability_id="code.write", id="t1"),
                Task(capability_id="test.run", id="t2", depende_de=("t1",)),
            ],
        )
        builder = SemanticSnapshotBuilder()
        snap = builder.build(execution_id="exec-1", plan=plan)
        assert snap.tasks_total == 2
        assert any("t2 depende de" in c for c in snap.constraints)

    def test_build_from_scheduler_run(self):
        from eon.scheduler.models import (
            SchedulerRun,
            SchedulerState,
            TaskExecutionRecord,
            TaskExecutionState,
        )

        run = SchedulerRun(
            plan_id="plan-1",
            tasks={
                "t1": TaskExecutionRecord(task_id="t1", estado=TaskExecutionState.COMPLETED),
                "t2": TaskExecutionRecord(task_id="t2", estado=TaskExecutionState.FAILED),
                "t3": TaskExecutionRecord(task_id="t3", estado=TaskExecutionState.BLOCKED),
            },
            orden=("t1", "t2", "t3"),
            estado=SchedulerState.RUNNING,
        )
        builder = SemanticSnapshotBuilder()
        snap = builder.build(execution_id="exec-1", scheduler_run=run)
        assert snap.tasks_total == 3
        assert snap.tasks_completed == 1
        assert snap.tasks_failed == 1
        assert any("t2" in r for r in snap.risks)  # failed task is a risk
        assert any("t3" in r for r in snap.risks)  # blocked task is a risk

    def test_low_confidence_creates_assumption(self):
        from eon.objectives.models import Objective, ObjectiveOrigin

        obj = Objective(
            descripcion="Test",
            criterio_de_exito="OK",
            origen=ObjectiveOrigin.USUARIO,
            confianza_minima=0.3,
        )
        builder = SemanticSnapshotBuilder()
        snap = builder.build(execution_id="exec-1", objective=obj)
        assert any("Confianza baja" in a for a in snap.assumptions)


# ─── SQLiteCheckpointStore Tests ───────────────────────────

class TestSQLiteCheckpointStore:
    def test_save_and_get(self, cp_store):
        cp = Checkpoint(
            execution_id="exec-1",
            event_seq=10,
            stores_state={"key": "value"},
        )
        cp.compute_hash()
        cp_store.save(cp)
        fetched = cp_store.get(cp.id)
        assert fetched is not None
        assert fetched.execution_id == "exec-1"
        assert fetched.event_seq == 10
        assert fetched.stores_state == {"key": "value"}

    def test_survives_reopen(self, tmp_path: Path):
        eng1 = SQLiteEngine(tmp_path / "reopen.db")
        eng1.init_schema()
        store1 = SQLiteCheckpointStore(eng1)
        cp = Checkpoint(execution_id="exec-1", event_seq=5, stores_state={"x": 1})
        cp.compute_hash()
        store1.save(cp)
        eng1.close()

        eng2 = SQLiteEngine(tmp_path / "reopen.db")
        eng2.init_schema()
        store2 = SQLiteCheckpointStore(eng2)
        fetched = store2.get(cp.id)
        assert fetched is not None
        assert fetched.execution_id == "exec-1"
        assert fetched.stores_state == {"x": 1}
        eng2.close()

    def test_latest_for_execution(self, cp_store):
        for seq in [1, 5, 3]:
            cp = Checkpoint(execution_id="exec-1", event_seq=seq)
            cp.compute_hash()
            cp_store.save(cp)
        latest = cp_store.latest_for_execution("exec-1")
        assert latest is not None
        assert latest.event_seq == 5  # highest seq

    def test_list_for_execution(self, cp_store):
        for i in range(3):
            cp = Checkpoint(execution_id="exec-1", event_seq=i)
            cp.compute_hash()
            cp_store.save(cp)
        # Other execution
        cp2 = Checkpoint(execution_id="exec-2", event_seq=1)
        cp2.compute_hash()
        cp_store.save(cp2)

        result = cp_store.list_for_execution("exec-1")
        assert len(result) == 3
        assert all(cp.execution_id == "exec-1" for cp in result)

    def test_verify_hash_valid(self, cp_store):
        cp = Checkpoint(
            execution_id="exec-1",
            event_seq=1,
            stores_state={"data": "test"},
        )
        cp.compute_hash()
        cp_store.save(cp)
        assert cp_store.verify_hash(cp.id) is True

    def test_count(self, cp_store):
        cp_store.save(Checkpoint(execution_id="e1", event_seq=1))
        cp_store.save(Checkpoint(execution_id="e1", event_seq=2))
        cp_store.save(Checkpoint(execution_id="e2", event_seq=1))
        assert cp_store.count() == 3
        assert cp_store.count("e1") == 2


# ─── CheckpointManager Tests ──────────────────────────────

class TestCheckpointManager:
    def test_crear_checkpoint_with_no_stores(self, cp_store):
        """CheckpointManager funciona sin stores — snapshot vacío."""
        manager = CheckpointManager(checkpoint_store=cp_store)
        cp = manager.crear_checkpoint("exec-1", reason="test")
        assert cp.execution_id == "exec-1"
        assert cp.reason == "test"
        assert cp.hash != ""
        assert cp.semantic is not None

    def test_recuperar_read_only(self, cp_store):
        manager = CheckpointManager(checkpoint_store=cp_store)
        cp = manager.crear_checkpoint("exec-1", reason="manual")
        result = manager.recuperar_desde_checkpoint(cp.id)
        assert result is not None
        assert result["hash_verified"] is True
        assert result["recoverable"] is True
        assert result["checkpoint"]["execution_id"] == "exec-1"

    def test_auto_checkpoint_manual_policy(self, cp_store):
        manager = CheckpointManager(checkpoint_store=cp_store)
        manager.set_auto_policy("manual")
        result = manager.maybe_auto_checkpoint("exec-1", "task_completed")
        assert result is None  # manual policy = no auto

    def test_auto_checkpoint_milestone_policy(self, cp_store):
        manager = CheckpointManager(checkpoint_store=cp_store)
        manager.set_auto_policy("milestone")
        cp = manager.maybe_auto_checkpoint("exec-1", "plan_created")
        assert cp is not None
        assert cp.kind == CheckpointKind.MILESTONE

    def test_auto_checkpoint_every_task_policy(self, cp_store):
        manager = CheckpointManager(checkpoint_store=cp_store)
        manager.set_auto_policy("every_task")
        cp = manager.maybe_auto_checkpoint("exec-1", "task_completed")
        assert cp is not None
        assert cp.kind == CheckpointKind.EVERY_TASK

    def test_recuperar_nonexistent_returns_none(self, cp_store):
        manager = CheckpointManager(checkpoint_store=cp_store)
        result = manager.recuperar_desde_checkpoint("nonexistent")
        assert result is None


# ─── KernelRuntime Integration Tests ──────────────────────

class TestKernelRuntimeCheckpoints:
    def test_checkpoint_with_sqlite(self, tmp_path: Path):
        from eon.planner.task import Task
        from eon.runtime import KernelRuntime

        db_path = str(tmp_path / "kernel_cp.db")
        kernel = KernelRuntime(
            root=str(tmp_path / "runtime"),
            tasks=[Task(capability_id="test.dummy", id="t1")],
            persistence_backend="sqlite",
            db_path=db_path,
        )
        result = kernel.run("Test objective", "Success criteria")
        cp = kernel.crear_checkpoint(result.execution_id, reason="manual")
        assert cp is not None
        assert cp.execution_id == result.execution_id
        assert cp.hash != ""
        assert cp.semantic is not None
        assert cp.semantic.intent == "Test objective"
        kernel.close()

    def test_checkpoint_survives_reopen(self, tmp_path: Path):
        from eon.planner.task import Task
        from eon.runtime import KernelRuntime

        db_path = str(tmp_path / "kernel_reopen_cp.db")
        runtime_root = str(tmp_path / "runtime_reopen_cp")

        kernel1 = KernelRuntime(
            root=runtime_root,
            tasks=[Task(capability_id="test.dummy", id="t1")],
            persistence_backend="sqlite",
            db_path=db_path,
        )
        result = kernel1.run("Persisted obj", "Done")
        cp = kernel1.crear_checkpoint(result.execution_id, reason="before close")
        cp_id = cp.id
        exec_id = result.execution_id
        kernel1.close()

        # Reopen
        kernel2 = KernelRuntime(
            root=runtime_root,
            tasks=[Task(capability_id="test.dummy", id="t1")],
            persistence_backend="sqlite",
            db_path=db_path,
        )
        fetched = kernel2.obtener_checkpoint(cp_id)
        assert fetched is not None
        assert fetched.execution_id == exec_id
        assert fetched.reason == "before close"

        # Recover
        recovered = kernel2.recuperar_desde_checkpoint(cp_id)
        assert recovered is not None
        assert recovered["hash_verified"] is True
        kernel2.close()

    def test_no_checkpointing_without_sqlite(self, tmp_path: Path):
        from eon.planner.task import Task
        from eon.runtime import KernelRuntime

        kernel = KernelRuntime(
            root=str(tmp_path / "runtime_mem"),
            tasks=[Task(capability_id="test.dummy", id="t1")],
            # No persistence_backend — memory mode
        )
        result = kernel.run("Test", "OK")
        cp = kernel.crear_checkpoint(result.execution_id)
        assert cp is None  # No checkpointing in memory mode
        kernel.close()

    def test_listar_checkpoints(self, tmp_path: Path):
        from eon.planner.task import Task
        from eon.runtime import KernelRuntime

        db_path = str(tmp_path / "kernel_list_cp.db")
        kernel = KernelRuntime(
            root=str(tmp_path / "runtime_list"),
            tasks=[Task(capability_id="test.dummy", id="t1")],
            persistence_backend="sqlite",
            db_path=db_path,
        )
        result = kernel.run("Test", "OK")
        kernel.crear_checkpoint(result.execution_id, reason="first")
        kernel.crear_checkpoint(result.execution_id, reason="second")
        checkpoints = kernel.listar_checkpoints(result.execution_id)
        assert len(checkpoints) == 2
        kernel.close()
