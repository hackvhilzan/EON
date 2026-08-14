"""
Tests de Execution Forking (Fase 5).

Criterios de aceptación:
- fork_from_checkpoint crea una nueva ejecución sin mutar la original
- El fork preserva metadatos del parent (parent_execution_id, checkpoint_id, seq)
- Hash verification: no se puede fork desde un checkpoint con hash inválido
- fork_from_checkpoint con checkpoint inexistente → None
- Múltiples forks desde el mismo checkpoint
- comparar_forks devuelve diferencias entre dos forks
- obtener_fork_tree construye jerarquía de forks
- Persistencia: fork sobrevive reinicio
- Evento execution.forked se emite al EventStore
- KernelRuntime.fork_from_checkpoint() integra correctamente
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eon.checkpoint import Checkpoint, CheckpointKind, SQLiteCheckpointStore
from eon.checkpoint.models import SemanticSnapshot
from eon.forking import ForkManager, ForkStatus, SQLiteForkStore
from eon.forking.models import ExecutionFork
from eon.persistence import SQLiteEngine
from eon.persistence.event_store import EventStore


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> SQLiteEngine:
    eng = SQLiteEngine(tmp_path / "test_fork.db")
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
def fork_store(engine: SQLiteEngine) -> SQLiteForkStore:
    return SQLiteForkStore(engine)


@pytest.fixture
def fork_manager(
    fork_store: SQLiteForkStore,
    cp_store: SQLiteCheckpointStore,
    event_store: EventStore,
) -> ForkManager:
    return ForkManager(fork_store, cp_store, event_store)


def _make_checkpoint(
    execution_id: str = "exec-1",
    event_seq: int = 5,
    stores_state: dict | None = None,
) -> Checkpoint:
    cp = Checkpoint(
        execution_id=execution_id,
        event_seq=event_seq,
        kind=CheckpointKind.MILESTONE,
        stores_state=stores_state or {
            "coordinator": {"estado": "running", "id": "exec-1"},
            "objective": {"estado": "in_progress", "id": "obj-1"},
        },
        semantic=SemanticSnapshot(intent="test objective", confidence=0.8),
        reason="test checkpoint",
    )
    cp.compute_hash()
    return cp


# ─── ForkManager Tests ────────────────────────────────────


class TestForkManager:
    def test_fork_from_checkpoint_creates_new_execution(self, fork_manager, cp_store):
        """fork_from_checkpoint crea una nueva ejecución sin mutar la original."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=5)
        cp_store.save(cp)

        fork = fork_manager.fork_from_checkpoint(cp.id)

        assert fork is not None
        assert fork.parent_execution_id == "exec-1"
        assert fork.parent_checkpoint_id == cp.id
        assert fork.forked_at_seq == 5
        assert fork.new_execution_id != "exec-1"
        assert fork.new_execution_id != ""
        assert fork.status == ForkStatus.CREATED

    def test_fork_preserves_parent_metadata(self, fork_manager, cp_store):
        """El fork preserva metadatos del parent."""
        cp = _make_checkpoint(execution_id="exec-parent", event_seq=10)
        cp_store.save(cp)

        fork = fork_manager.fork_from_checkpoint(
            cp.id,
            new_objective="Modified objective for fork",
            metadata={"reason": "testing alternative approach"},
        )

        assert fork.parent_execution_id == "exec-parent"
        assert fork.parent_checkpoint_id == cp.id
        assert fork.forked_at_seq == 10
        assert fork.new_objective == "Modified objective for fork"
        assert fork.metadata["reason"] == "testing alternative approach"

    def test_fork_nonexistent_checkpoint_returns_none(self, fork_manager):
        """fork desde checkpoint inexistente → None."""
        result = fork_manager.fork_from_checkpoint("nonexistent-cp-id")
        assert result is None

    def test_fork_tampered_checkpoint_returns_none(self, fork_manager, cp_store):
        """No se puede fork desde un checkpoint con hash inválido."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=3)
        cp_store.save(cp)

        # Tamper: alterar el checkpoint en la DB
        import json
        row = cp_store._engine.query_one(
            "SELECT data FROM checkpoints WHERE id = ?", (cp.id,)
        )
        data = json.loads(row["data"])
        data["stores_state"]["coordinator"]["estado"] = "TAMPERED"
        cp_store._engine.execute(
            "UPDATE checkpoints SET data = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False, default=str), cp.id),
        )

        result = fork_manager.fork_from_checkpoint(cp.id)
        assert result is None

    def test_multiple_forks_from_same_checkpoint(self, fork_manager, cp_store):
        """Múltiples forks desde el mismo checkpoint."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=5)
        cp_store.save(cp)

        fork1 = fork_manager.fork_from_checkpoint(cp.id, new_objective="strategy A")
        fork2 = fork_manager.fork_from_checkpoint(cp.id, new_objective="strategy B")
        fork3 = fork_manager.fork_from_checkpoint(cp.id, new_objective="strategy C")

        assert fork1 is not None
        assert fork2 is not None
        assert fork3 is not None
        # Cada fork tiene su propio new_execution_id
        assert fork1.new_execution_id != fork2.new_execution_id
        assert fork2.new_execution_id != fork3.new_execution_id
        assert fork1.new_execution_id != fork3.new_execution_id

        # Todos comparten el mismo parent
        forks = fork_manager.listar_forks("exec-1")
        assert len(forks) == 3

    def test_fork_emits_event(self, fork_manager, cp_store, event_store):
        """fork_from_checkpoint emite evento execution.forked."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=5)
        cp_store.save(cp)

        # Añadir evento previo para que el event_store tenga sec
        event_store.append("exec-1", "coordinator.created", {})

        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        # Buscar el evento execution.forked
        events = event_store.get_events(execution_id="exec-1")
        fork_events = [e for e in events if e.event_type == "execution.forked"]
        assert len(fork_events) == 1
        assert fork_events[0].payload["fork_id"] == fork.id
        assert fork_events[0].payload["new_execution_id"] == fork.new_execution_id

    def test_obtener_fork(self, fork_manager, cp_store):
        """obtener_fork recupera un fork por ID."""
        cp = _make_checkpoint()
        cp_store.save(cp)
        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        retrieved = fork_manager.obtener_fork(fork.id)
        assert retrieved is not None
        assert retrieved.id == fork.id
        assert retrieved.new_execution_id == fork.new_execution_id

    def test_obtener_fork_nonexistent(self, fork_manager):
        """obtener_fork con ID inexistente → None."""
        assert fork_manager.obtener_fork("nonexistent") is None

    def test_listar_forks(self, fork_manager, cp_store):
        """listar_forks devuelve todos los forks de un parent."""
        cp = _make_checkpoint(execution_id="exec-1")
        cp_store.save(cp)

        fork_manager.fork_from_checkpoint(cp.id)
        fork_manager.fork_from_checkpoint(cp.id)

        forks = fork_manager.listar_forks("exec-1")
        assert len(forks) == 2

        # Forks de otra ejecución
        forks_other = fork_manager.listar_forks("exec-2")
        assert len(forks_other) == 0

    def test_actualizar_estado_fork(self, fork_manager, cp_store):
        """actualizar_estado_fork cambia el estado."""
        cp = _make_checkpoint()
        cp_store.save(cp)
        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        result = fork_manager.actualizar_estado_fork(fork.id, ForkStatus.COMPLETED)
        assert result is True

        updated = fork_manager.obtener_fork(fork.id)
        assert updated.status == ForkStatus.COMPLETED
        assert updated.completed_at is not None


# ─── comparar_forks Tests ─────────────────────────────────


class TestCompararForks:
    def test_compare_same_checkpoint_forks(self, fork_manager, cp_store):
        """Comparar dos forks del mismo checkpoint."""
        cp = _make_checkpoint(
            execution_id="exec-1",
            event_seq=5,
            stores_state={"coordinator": {"estado": "running"}},
        )
        cp_store.save(cp)

        fork_a = fork_manager.fork_from_checkpoint(cp.id, new_objective="A")
        fork_b = fork_manager.fork_from_checkpoint(cp.id, new_objective="B")

        result = fork_manager.comparar_forks(fork_a.id, fork_b.id)

        assert result is not None
        assert result["same_parent"] is True
        assert result["same_checkpoint"] is True
        assert result["differences"]["semantic"]["same_intent"] is True
        assert result["differences"]["semantic"]["same_confidence"] is True

    def test_compare_different_checkpoint_forks(self, fork_manager, cp_store):
        """Comparar forks de checkpoints diferentes."""
        cp1 = _make_checkpoint(
            execution_id="exec-1",
            event_seq=3,
            stores_state={"coordinator": {"estado": "planning"}},
        )
        cp1.semantic = SemanticSnapshot(intent="original", confidence=0.7)
        cp1.compute_hash()
        cp_store.save(cp1)

        cp2 = _make_checkpoint(
            execution_id="exec-1",
            event_seq=8,
            stores_state={"coordinator": {"estado": "running"}},
        )
        cp2.semantic = SemanticSnapshot(intent="modified", confidence=0.9)
        cp2.compute_hash()
        cp_store.save(cp2)

        fork_a = fork_manager.fork_from_checkpoint(cp1.id)
        fork_b = fork_manager.fork_from_checkpoint(cp2.id)

        result = fork_manager.comparar_forks(fork_a.id, fork_b.id)

        assert result is not None
        assert result["same_parent"] is True
        assert result["same_checkpoint"] is False
        assert result["differences"]["semantic"]["same_intent"] is False
        assert result["differences"]["semantic"]["same_confidence"] is False
        assert result["differences"]["coordinator"]["same_state"] is False

    def test_compare_nonexistent_fork(self, fork_manager):
        """Comparar con fork inexistente → None."""
        result = fork_manager.comparar_forks("nonexistent-a", "nonexistent-b")
        assert result is None


# ─── Fork Tree Tests ──────────────────────────────────────


class TestForkTree:
    def test_fork_tree_simple(self, fork_manager, cp_store):
        """obtener_fork_tree con forks directos."""
        cp = _make_checkpoint(execution_id="exec-1")
        cp_store.save(cp)

        fork1 = fork_manager.fork_from_checkpoint(cp.id)
        fork2 = fork_manager.fork_from_checkpoint(cp.id)

        tree = fork_manager.obtener_fork_tree("exec-1")

        assert tree["execution_id"] == "exec-1"
        assert len(tree["children"]) == 2
        assert tree["children"][0]["fork_id"] == fork1.id
        assert tree["children"][1]["fork_id"] == fork2.id

    def test_fork_tree_no_children(self, fork_manager):
        """obtener_fork_tree sin forks → children vacíos."""
        tree = fork_manager.obtener_fork_tree("exec-no-forks")
        assert tree["execution_id"] == "exec-no-forks"
        assert tree["children"] == []


# ─── Persistence Tests ────────────────────────────────────


class TestForkPersistence:
    def test_fork_survives_reopen(self, engine, cp_store, event_store):
        """El fork sobrevive un reinicio del proceso."""
        cp = _make_checkpoint(execution_id="exec-1")
        cp_store.save(cp)

        fork_store1 = SQLiteForkStore(engine)
        fm1 = ForkManager(fork_store1, cp_store, event_store)
        fork = fm1.fork_from_checkpoint(cp.id)
        assert fork is not None

        # Simular reinicio: nuevo store con mismo engine
        fork_store2 = SQLiteForkStore(engine)
        fm2 = ForkManager(fork_store2, cp_store, event_store)

        retrieved = fm2.obtener_fork(fork.id)
        assert retrieved is not None
        assert retrieved.new_execution_id == fork.new_execution_id
        assert retrieved.parent_execution_id == "exec-1"


# ─── SQLiteForkStore Tests ────────────────────────────────


class TestSQLiteForkStore:
    def test_save_and_get(self, fork_store):
        """save y get funcionan correctamente."""
        fork = ExecutionFork(
            parent_execution_id="exec-1",
            parent_checkpoint_id="cp-1",
            forked_at_seq=5,
            new_execution_id="exec-2",
            new_objective="test objective",
            metadata={"reason": "test"},
        )
        fork_store.save(fork)

        retrieved = fork_store.get(fork.id)
        assert retrieved is not None
        assert retrieved.parent_execution_id == "exec-1"
        assert retrieved.new_execution_id == "exec-2"
        assert retrieved.metadata["reason"] == "test"

    def test_list_for_parent(self, fork_store):
        """list_for_parent filtra por parent_execution_id."""
        fork1 = ExecutionFork(parent_execution_id="exec-1", new_execution_id="exec-2")
        fork2 = ExecutionFork(parent_execution_id="exec-1", new_execution_id="exec-3")
        fork3 = ExecutionFork(parent_execution_id="exec-2", new_execution_id="exec-4")
        fork_store.save(fork1)
        fork_store.save(fork2)
        fork_store.save(fork3)

        assert len(fork_store.list_for_parent("exec-1")) == 2
        assert len(fork_store.list_for_parent("exec-2")) == 1
        assert len(fork_store.list_for_parent("exec-3")) == 0

    def test_count(self, fork_store):
        """count devuelve el número correcto."""
        assert fork_store.count() == 0

        fork_store.save(ExecutionFork(parent_execution_id="exec-1", new_execution_id="exec-2"))
        assert fork_store.count() == 1
        assert fork_store.count("exec-1") == 1
        assert fork_store.count("exec-2") == 0

    def test_update_status(self, fork_store):
        """update_status actualiza el estado."""
        fork = ExecutionFork(parent_execution_id="exec-1", new_execution_id="exec-2")
        fork_store.save(fork)

        result = fork_store.update_status(fork.id, "completed", "2026-01-01T00:00:00Z")
        assert result is True

        updated = fork_store.get(fork.id)
        assert updated.status == ForkStatus.COMPLETED
        assert updated.completed_at == "2026-01-01T00:00:00Z"

    def test_list_for_new_execution(self, fork_store):
        """list_for_new_execution encuentra el fork que creó una ejecución."""
        fork = ExecutionFork(parent_execution_id="exec-1", new_execution_id="exec-2")
        fork_store.save(fork)

        result = fork_store.list_for_new_execution("exec-2")
        assert result is not None
        assert result.id == fork.id

        assert fork_store.list_for_new_execution("exec-3") is None


# ─── KernelRuntime Integration Tests ──────────────────────


class TestKernelRuntimeForking:
    def test_fork_without_sqlite(self, tmp_path):
        """Sin SQLite, fork_from_checkpoint devuelve None."""
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(root=tmp_path / "runtime")
        try:
            assert runtime.fork_from_checkpoint("cp-1") is None
            assert runtime.listar_forks("exec-1") == []
            assert runtime.comparar_forks("a", "b") is None
            assert runtime.obtener_fork_tree("exec-1") is None
        finally:
            runtime.close()

    def test_fork_with_sqlite(self, tmp_path):
        """Con SQLite, fork_from_checkpoint funciona."""
        from eon.runtime import KernelRuntime

        runtime = KernelRuntime(
            root=tmp_path / "runtime",
            persistence_backend="sqlite",
            db_path=str(tmp_path / "eon.db"),
            checkpointing_enabled=True,
        )
        try:
            # Crear checkpoint manualmente
            cp = Checkpoint(
                execution_id="exec-1",
                event_seq=1,
                kind=CheckpointKind.MANUAL,
                stores_state={"coordinator": {"estado": "running"}},
                semantic=SemanticSnapshot(intent="test"),
            )
            cp.compute_hash()
            runtime._checkpoint_manager._store.save(cp)

            fork = runtime.fork_from_checkpoint(cp.id)
            assert fork is not None
            assert fork.parent_execution_id == "exec-1"
            assert fork.new_execution_id != "exec-1"

            # Listar forks
            forks = runtime.listar_forks("exec-1")
            assert len(forks) == 1
        finally:
            runtime.close()


# ─── State Copy Tests ─────────────────────────────────────


class TestForkStateCopy:
    """Tests que verifican que el fork copia estado real."""

    def test_fork_creates_recoverable_checkpoint(self, fork_manager, cp_store):
        """El fork crea un checkpoint inicial para la nueva ejecución."""
        cp = _make_checkpoint(
            execution_id="exec-1",
            event_seq=5,
            stores_state={
                "coordinator": {"estado": "running", "id": "exec-1"},
                "objective": {"estado": "in_progress", "id": "obj-1"},
            },
        )
        cp_store.save(cp)

        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        # La nueva ejecución debe tener un checkpoint recuperable
        new_cps = cp_store.latest_for_execution(fork.new_execution_id)
        assert new_cps is not None
        assert new_cps.execution_id == fork.new_execution_id
        assert new_cps.stores_state == cp.stores_state

    def test_fork_does_not_mutate_original(self, fork_manager, cp_store):
        """El fork no muta el checkpoint original."""
        original_state = {
            "coordinator": {"estado": "running", "id": "exec-1"},
            "objective": {"estado": "in_progress", "id": "obj-1"},
        }
        cp = _make_checkpoint(
            execution_id="exec-1",
            event_seq=5,
            stores_state=original_state,
        )
        cp_store.save(cp)

        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        # Verificar que el checkpoint original no cambió
        original_cp = cp_store.get(cp.id)
        assert original_cp.stores_state == original_state
        assert original_cp.execution_id == "exec-1"

    def test_fork_deep_copies_state(self, fork_manager, cp_store):
        """El fork hace deep-copy (no comparte referencias)."""
        state = {"coordinator": {"estado": "running", "nested": [1, 2, 3]}}
        cp = _make_checkpoint(
            execution_id="exec-1", event_seq=3, stores_state=state
        )
        cp_store.save(cp)

        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        new_cp = cp_store.latest_for_execution(fork.new_execution_id)
        assert new_cp is not None

        # Modificar el estado del fork no afecta al original
        new_cp.stores_state["coordinator"]["nested"].append(999)
        original_cp = cp_store.get(cp.id)
        assert original_cp.stores_state["coordinator"]["nested"] == [1, 2, 3]

    def test_fork_preserves_semantic_snapshot(self, fork_manager, cp_store):
        """El fork preserva el SemanticSnapshot del checkpoint original."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=5)
        cp_store.save(cp)

        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        new_cp = cp_store.latest_for_execution(fork.new_execution_id)
        assert new_cp is not None
        assert new_cp.semantic is not None
        assert new_cp.semantic.intent == cp.semantic.intent
        assert new_cp.semantic.confidence == cp.semantic.confidence

    def test_fork_event_emitted_for_new_execution(self, fork_manager, cp_store, event_store):
        """El evento execution.forked se emite con el nuevo execution_id."""
        cp = _make_checkpoint(execution_id="exec-1", event_seq=5)
        cp_store.save(cp)

        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        events = event_store.get_events(fork.new_execution_id)
        assert any(e.event_type == "execution.forked" for e in events)

    def test_fork_new_execution_has_recoverable_checkpoint(self, fork_manager, cp_store):
        """La nueva ejecución del fork tiene un checkpoint recuperable."""
        cp = _make_checkpoint(
            execution_id="exec-1",
            event_seq=5,
            stores_state={
                "coordinator": {"estado": "running", "id": "exec-1"},
                "objective": {"estado": "in_progress"},
            },
        )
        cp_store.save(cp)

        fork = fork_manager.fork_from_checkpoint(cp.id)
        assert fork is not None

        # La nueva ejecución tiene un checkpoint con el estado copiado
        new_cp = cp_store.latest_for_execution(fork.new_execution_id)
        assert new_cp is not None
        assert new_cp.execution_id == fork.new_execution_id
        assert new_cp.stores_state["coordinator"]["estado"] == "running"
        assert new_cp.stores_state["objective"]["estado"] == "in_progress"

        # El hash del nuevo checkpoint verifica
        assert cp_store.verify_hash(new_cp.id)
