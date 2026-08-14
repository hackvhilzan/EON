"""
Tests de Time Travel (Fase 4).

Criterios de aceptación:
- reconstruct_state con checkpoint base → complete=True, stores_state poblado
- reconstruct_state sin checkpoint base → complete=False, timeline de eventos
- latest_before_seq encuentra el checkpoint correcto
- Events timeline bounded por target_event_seq
- Applied vs unapplied events clasificados correctamente
- Hash verification del checkpoint base
- get_timeline devuelve eventos en rango
- KernelRuntime.inspeccionar_en() integra correctamente
- Conservadores: reducers no mutan el checkpoint original
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eon.checkpoint import Checkpoint, CheckpointKind, SQLiteCheckpointStore
from eon.checkpoint.models import SemanticSnapshot
from eon.persistence import SQLiteEngine
from eon.persistence.event_store import EventStore
from eon.timetravel import TimeMachine

# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> SQLiteEngine:
    eng = SQLiteEngine(tmp_path / "test_tt.db")
    eng.init_schema()
    yield eng
    eng.close()


@pytest.fixture
def cp_store(engine: SQLiteEngine) -> SQLiteCheckpointStore:
    return SQLiteCheckpointStore(engine)


@pytest.fixture
def event_store(engine: SQLiteEngine) -> EventStore:
    return EventStore(engine)


@pytest.fixture
def time_machine(cp_store: SQLiteCheckpointStore, event_store: EventStore) -> TimeMachine:
    return TimeMachine(cp_store, event_store)


# ─── Helpers ──────────────────────────────────────────────


def _make_checkpoint(
    execution_id: str = "exec-1",
    event_seq: int = 5,
    stores_state: dict | None = None,
) -> Checkpoint:
    cp = Checkpoint(
        execution_id=execution_id,
        event_seq=event_seq,
        kind=CheckpointKind.MILESTONE,
        stores_state=stores_state or {"coordinator": {"estado": "running", "id": "exec-1"}},
        semantic=SemanticSnapshot(intent="test objective", confidence=0.8),
        reason="test checkpoint",
    )
    cp.compute_hash()
    return cp


# ─── TimeMachine Tests ────────────────────────────────────


class TestTimeMachine:
    def test_reconstruct_with_checkpoint_base(self, time_machine, cp_store, event_store):
        """Reconstrucción con checkpoint base → complete=True."""
        # Añadir algunos eventos primero para que el checkpoint tenga un event_seq real
        event_store.append("exec-1", "coordinator.created", {"execution_id": "exec-1"})
        event_store.append("exec-1", "plan.created", {"plan_id": "plan-1"})
        seq_at_checkpoint = event_store.get_last_seq()

        cp = _make_checkpoint(execution_id="exec-1", event_seq=seq_at_checkpoint)
        cp_store.save(cp)

        # Eventos posteriores al checkpoint
        event_store.append("exec-1", "coordinator.transition", {"from": "running", "to": "verifying"})
        event_store.append("exec-1", "task.completed", {"task_id": "task-1"})

        state = time_machine.reconstruct_state("exec-1", event_seq=100)

        assert state is not None
        assert state.complete is True
        assert state.base_checkpoint_id == cp.id
        assert state.base_checkpoint_seq == seq_at_checkpoint
        assert state.hash_verified is True
        assert state.stores_state != {}
        assert "coordinator" in state.stores_state
        assert state.total_events_replayed == 2

    def test_reconstruct_without_checkpoint_base(self, time_machine, cp_store, event_store):
        """Sin checkpoint base → complete=False, timeline de eventos."""
        event_store.append("exec-1", "coordinator.transition", {"from": "created", "to": "planning"})
        event_store.append("exec-1", "plan.created", {"plan_id": "plan-1"})

        state = time_machine.reconstruct_state("exec-1", event_seq=10)

        assert state.complete is False
        assert state.base_checkpoint_id is None
        assert state.stores_state == {}
        assert state.semantic is None
        assert len(state.events_timeline) == 2
        assert state.total_events_replayed == 2

    def test_reconstruct_at_exact_checkpoint_seq(self, time_machine, cp_store, event_store):
        """Reconstrucción exactamente en el event_seq del checkpoint."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=3)
        cp_store.save(cp)

        # Eventos posteriores al checkpoint
        event_store.append("exec-1", "task.completed", {"task_id": "t1"})
        event_store.append("exec-1", "task.completed", {"task_id": "t2"})

        # Reconstruir exactamente en el seq del checkpoint
        state = time_machine.reconstruct_state("exec-1", event_seq=3)

        assert state.complete is True
        assert state.base_checkpoint_seq == 3
        # No hay eventos entre seq=3 y seq=3 (after_seq=3, <=3 → vacío)
        assert state.total_events_replayed == 0
        assert len(state.events_timeline) == 0

    def test_reconstruct_picks_correct_checkpoint(self, time_machine, cp_store, event_store):
        """Debe usar el último checkpoint con seq <= target_seq."""
        cp1 = _make_checkpoint(execution_id="exec-1", event_seq=3)
        cp1.stores_state = {"coordinator": {"estado": "planning"}}
        cp1.compute_hash()
        cp_store.save(cp1)

        cp2 = _make_checkpoint(execution_id="exec-1", event_seq=8)
        cp2.stores_state = {"coordinator": {"estado": "running"}}
        cp2.compute_hash()
        cp_store.save(cp2)

        # Eventos entre cp1 y cp2
        event_store.append("exec-1", "coordinator.transition", {"from": "planning", "to": "scheduling"})
        # Eventos después de cp2
        event_store.append("exec-1", "task.completed", {"task_id": "t1"})

        # Reconstruir en seq=9: debe usar cp2 (seq=8)
        state = time_machine.reconstruct_state("exec-1", event_seq=9)
        assert state.base_checkpoint_id == cp2.id
        assert state.base_checkpoint_seq == 8

        # Reconstruir en seq=5: debe usar cp1 (seq=3)
        state2 = time_machine.reconstruct_state("exec-1", event_seq=5)
        assert state2.base_checkpoint_id == cp1.id
        assert state2.base_checkpoint_seq == 3

    def test_events_bounded_by_target_seq(self, time_machine, cp_store, event_store):
        """Events timeline no debe exceder target_seq."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=2)
        cp_store.save(cp)

        # Añadir 5 eventos (seq 3,4,5,6,7)
        for i in range(5):
            event_store.append("exec-1", "task.completed", {"task_id": f"t{i}"})

        # Reconstruir en seq=5: solo debe incluir eventos hasta seq=5
        state = time_machine.reconstruct_state("exec-1", event_seq=5)

        assert state.total_events_replayed == 3  # seq 3,4,5
        for event in state.events_timeline:
            assert event["seq"] <= 5

    def test_applied_vs_unapplied_events(self, time_machine, cp_store, event_store):
        """Eventos conocidos van a applied, desconocidos a unapplied."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=0)
        cp_store.save(cp)

        event_store.append("exec-1", "coordinator.transition", {"from": "running", "to": "verifying"})
        event_store.append("exec-1", "task.completed", {"task_id": "t1"})
        event_store.append("exec-1", "unknown.event.type", {"data": "test"})

        state = time_machine.reconstruct_state("exec-1", event_seq=10)

        assert len(state.applied_events) == 2  # coordinator.transition, task.completed
        assert len(state.unapplied_events) == 1  # unknown.event.type
        assert state.unapplied_events[0]["event_type"] == "unknown.event.type"

    def test_hash_verification_failure(self, time_machine, cp_store, event_store):
        """Si el hash del checkpoint no verifica, complete=False."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=2)
        cp_store.save(cp)

        # Tamper: alterar el checkpoint directamente en la DB
        import json

        row = cp_store._engine.query_one("SELECT data FROM checkpoints WHERE id = ?", (cp.id,))
        data = json.loads(row["data"])
        data["stores_state"]["coordinator"]["estado"] = "TAMPERED"
        cp_store._engine.execute(
            "UPDATE checkpoints SET data = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False, default=str), cp.id),
        )

        event_store.append("exec-1", "task.completed", {"task_id": "t1"})

        state = time_machine.reconstruct_state("exec-1", event_seq=10)

        # El hash no verifica → complete=False
        assert state.hash_verified is False
        assert state.complete is False

    def test_get_timeline(self, time_machine, cp_store, event_store):
        """get_timeline devuelve eventos en rango."""
        for i in range(10):
            event_store.append("exec-1", "task.completed", {"task_id": f"t{i}"})

        # Timeline completo
        timeline = time_machine.get_timeline("exec-1")
        assert len(timeline) == 10

        # Timeline con rango
        timeline_ranged = time_machine.get_timeline("exec-1", from_seq=3, to_seq=7)
        assert len(timeline_ranged) == 4  # seq 4,5,6,7
        for event in timeline_ranged:
            assert 4 <= event["seq"] <= 7

    def test_reconstruct_does_not_mutate_checkpoint(self, time_machine, cp_store, event_store):
        """La reconstrucción es read-only: no muta el checkpoint original."""
        cp = _make_checkpoint(
            execution_id="exec-1",
            event_seq=2,
            stores_state={"coordinator": {"estado": "running", "id": "exec-1"}},
        )
        cp_store.save(cp)
        original_state = dict(cp.stores_state["coordinator"])

        event_store.append("exec-1", "coordinator.transition", {"from": "running", "to": "verifying"})

        state = time_machine.reconstruct_state("exec-1", event_seq=10)

        # El checkpoint original no debe haber cambiado
        cp_after = cp_store.get(cp.id)
        assert cp_after.stores_state["coordinator"]["estado"] == original_state["estado"]

    def test_reconstruct_empty_execution(self, time_machine, event_store):
        """Reconstrucción de ejecución sin eventos ni checkpoints."""
        state = time_machine.reconstruct_state("nonexistent", event_seq=100)

        assert state.complete is False
        assert state.total_events_replayed == 0
        assert len(state.events_timeline) == 0

    def test_reconstruct_multiple_executions(self, time_machine, cp_store, event_store):
        """Reconstrucción aísla eventos por execution_id."""
        # exec-1: 1 evento antes del checkpoint, 2 después
        event_store.append("exec-1", "coordinator.created", {"execution_id": "exec-1"})
        seq1 = event_store.get_last_seq()
        cp1 = _make_checkpoint(execution_id="exec-1", event_seq=seq1)
        cp_store.save(cp1)

        # exec-2: 1 evento antes del checkpoint, 1 después
        event_store.append("exec-2", "coordinator.created", {"execution_id": "exec-2"})
        seq2 = event_store.get_last_seq()
        cp2 = _make_checkpoint(execution_id="exec-2", event_seq=seq2)
        cp_store.save(cp2)

        # Eventos posteriores
        event_store.append("exec-1", "task.completed", {"task_id": "t1"})
        event_store.append("exec-2", "task.completed", {"task_id": "t2"})
        event_store.append("exec-1", "task.completed", {"task_id": "t3"})

        state1 = time_machine.reconstruct_state("exec-1", event_seq=100)
        state2 = time_machine.reconstruct_state("exec-2", event_seq=100)

        # Cada ejecución solo ve sus propios eventos posteriores al checkpoint
        assert all(e["execution_id"] == "exec-1" for e in state1.events_timeline)
        assert all(e["execution_id"] == "exec-2" for e in state2.events_timeline)
        assert state1.total_events_replayed == 2  # t1, t3
        assert state2.total_events_replayed == 1  # t2


# ─── CheckpointStore.latest_before_seq Tests ──────────────


class TestLatestBeforeSeq:
    def test_finds_correct_checkpoint(self, cp_store):
        """latest_before_seq devuelve el checkpoint correcto."""
        cp1 = _make_checkpoint(execution_id="exec-1", event_seq=3)
        cp_store.save(cp1)
        cp2 = _make_checkpoint(execution_id="exec-1", event_seq=8)
        cp_store.save(cp2)
        cp3 = _make_checkpoint(execution_id="exec-1", event_seq=15)
        cp_store.save(cp3)

        # Buscar antes de seq=10 → debe dar cp2 (seq=8)
        result = cp_store.latest_before_seq("exec-1", 10)
        assert result is not None
        assert result.id == cp2.id

        # Buscar antes de seq=3 → debe dar cp1 (seq=3)
        result = cp_store.latest_before_seq("exec-1", 3)
        assert result is not None
        assert result.id == cp1.id

        # Buscar antes de seq=2 → None (no hay checkpoint con seq<=2)
        result = cp_store.latest_before_seq("exec-1", 2)
        assert result is None

    def test_different_executions(self, cp_store):
        """latest_before_seq aísla por execution_id."""
        cp1 = _make_checkpoint(execution_id="exec-1", event_seq=5)
        cp_store.save(cp1)
        cp2 = _make_checkpoint(execution_id="exec-2", event_seq=5)
        cp_store.save(cp2)

        result = cp_store.latest_before_seq("exec-1", 10)
        assert result is not None
        assert result.execution_id == "exec-1"

        result = cp_store.latest_before_seq("exec-2", 10)
        assert result is not None
        assert result.execution_id == "exec-2"

        result = cp_store.latest_before_seq("exec-3", 10)
        assert result is None


# ─── EventStore.get_events_until Tests ─────────────────────


class TestGetEventsUntil:
    def test_bounded_range(self, event_store):
        """get_events_until respeta el rango [from_seq+1, to_seq]."""
        for i in range(10):
            event_store.append("exec-1", "task.completed", {"task_id": f"t{i}"})

        events = event_store.get_events_until(
            execution_id="exec-1",
            from_seq=2,
            to_seq=6,
        )
        assert len(events) == 4  # seq 3,4,5,6
        for e in events:
            assert 3 <= e.seq <= 6

    def test_no_upper_bound(self, event_store):
        """to_seq=None lee hasta el último evento."""
        for i in range(5):
            event_store.append("exec-1", "task.completed", {"task_id": f"t{i}"})

        events = event_store.get_events_until(
            execution_id="exec-1",
            from_seq=2,
            to_seq=None,
        )
        assert len(events) == 3  # seq 3,4,5

    def test_filter_by_type(self, event_store):
        """Filtrar por event_type en get_events_until."""
        event_store.append("exec-1", "task.completed", {"task_id": "t1"})
        event_store.append("exec-1", "task.failed", {"task_id": "t2"})
        event_store.append("exec-1", "task.completed", {"task_id": "t3"})

        events = event_store.get_events_until(
            execution_id="exec-1",
            event_type="task.completed",
        )
        assert len(events) == 2
        assert all(e.event_type == "task.completed" for e in events)


# ─── KernelRuntime Integration Tests ──────────────────────


class TestKernelRuntimeTimeTravel:
    def test_inspeccionar_en_without_sqlite(self, tmp_path):
        """Sin SQLite, inspeccionar_en devuelve None."""
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=tmp_path / "runtime")
        try:
            result = runtime.inspeccionar_en("exec-1", 10)
            assert result is None
        finally:
            runtime.close()

    def test_inspeccionar_en_with_sqlite(self, tmp_path):
        """Con SQLite, inspeccionar_en devuelve ReconstructedState."""
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(
            root=tmp_path / "runtime",
            persistence_backend="sqlite",
            db_path=str(tmp_path / "eon.db"),
            checkpointing_enabled=True,
        )
        try:
            # Crear un checkpoint manualmente
            from eon.checkpoint import Checkpoint, CheckpointKind
            from eon.checkpoint.models import SemanticSnapshot

            cp = Checkpoint(
                execution_id="exec-1",
                event_seq=1,
                kind=CheckpointKind.MANUAL,
                stores_state={"coordinator": {"estado": "running"}},
                semantic=SemanticSnapshot(intent="test"),
            )
            cp.compute_hash()
            runtime._checkpoint_manager._store.save(cp)

            # Añadir evento
            runtime._store_registry.event_store.append("exec-1", "task.completed", {"task_id": "t1"})

            result = runtime.inspeccionar_en("exec-1", 10)
            assert result is not None
            assert result.complete is True
            assert result.base_checkpoint_id == cp.id
        finally:
            runtime.close()

    def test_obtener_timeline_without_sqlite(self, tmp_path):
        """Sin SQLite, obtener_timeline devuelve lista vacía."""
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=tmp_path / "runtime")
        try:
            result = runtime.obtener_timeline("exec-1")
            assert result == []
        finally:
            runtime.close()
