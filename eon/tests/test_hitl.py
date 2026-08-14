"""
Tests de HITL Interrupt/Resume (Fase 3.5).

Criterios de aceptación:
- Interrupt create → close → reopen → pending survives
- Approve/deny transitions válidas
- Invalid transitions fallan
- Interrupt crea checkpoint cuando hay checkpoint manager
- Resume desde interrupt aprobado devuelve estado recuperable
- Sin SQLite: no rompe compatibilidad
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eon.hitl import HITLInterrupt, HITLManager, HITLStatus, SQLiteHITLStore
from eon.persistence import SQLiteEngine


@pytest.fixture
def engine(tmp_path: Path) -> SQLiteEngine:
    eng = SQLiteEngine(tmp_path / "test_hitl.db")
    eng.init_schema()
    yield eng
    eng.close()


@pytest.fixture
def hitl_store(engine: SQLiteEngine) -> SQLiteHITLStore:
    return SQLiteHITLStore(engine)


@pytest.fixture
def manager(hitl_store: SQLiteHITLStore) -> HITLManager:
    return HITLManager(hitl_store)


# ─── HITL Model Tests ──────────────────────────────────────

class TestHITLModel:
    def test_create_default(self):
        interrupt = HITLInterrupt(
            execution_id="exec-1",
            task_id="task-5",
            tool_name="tool.terminal",
            reason="REQUIRES_APPROVAL",
        )
        assert interrupt.status == HITLStatus.PENDING
        assert interrupt.id != ""
        assert interrupt.created_at != ""

    def test_valid_transition_pending_to_approved(self):
        interrupt = HITLInterrupt(execution_id="exec-1")
        interrupt.transition_to(HITLStatus.APPROVED, decided_by="admin")
        assert interrupt.status == HITLStatus.APPROVED
        assert interrupt.decided_by == "admin"
        assert interrupt.resolved_at is not None

    def test_valid_transition_approved_to_resumed(self):
        interrupt = HITLInterrupt(execution_id="exec-1")
        interrupt.transition_to(HITLStatus.APPROVED, decided_by="admin")
        interrupt.transition_to(HITLStatus.RESUMED)
        assert interrupt.status == HITLStatus.RESUMED

    def test_valid_transition_pending_to_denied(self):
        interrupt = HITLInterrupt(execution_id="exec-1")
        interrupt.transition_to(HITLStatus.DENIED, decided_by="admin")
        assert interrupt.status == HITLStatus.DENIED

    def test_invalid_transition_approved_to_approved(self):
        interrupt = HITLInterrupt(execution_id="exec-1")
        interrupt.transition_to(HITLStatus.APPROVED, decided_by="admin")
        with pytest.raises(ValueError, match="Transición inválida"):
            interrupt.transition_to(HITLStatus.APPROVED)

    def test_invalid_transition_denied_to_approved(self):
        interrupt = HITLInterrupt(execution_id="exec-1")
        interrupt.transition_to(HITLStatus.DENIED)
        with pytest.raises(ValueError, match="Transición inválida"):
            interrupt.transition_to(HITLStatus.APPROVED)

    def test_invalid_transition_resumed_to_anything(self):
        interrupt = HITLInterrupt(execution_id="exec-1")
        interrupt.transition_to(HITLStatus.APPROVED)
        interrupt.transition_to(HITLStatus.RESUMED)
        with pytest.raises(ValueError, match="Transición inválida"):
            interrupt.transition_to(HITLStatus.APPROVED)

    def test_to_dict_from_dict_roundtrip(self):
        interrupt = HITLInterrupt(
            execution_id="exec-1",
            task_id="task-3",
            tool_name="tool.python",
            reason="REQUIRES_APPROVAL",
            payload={"cmd": "eval('os.system(...)')"},
        )
        interrupt.transition_to(HITLStatus.APPROVED, decided_by="admin", decision_reason="OK")
        d = interrupt.to_dict()
        restored = HITLInterrupt.from_dict(d)
        assert restored.execution_id == "exec-1"
        assert restored.status == HITLStatus.APPROVED
        assert restored.decided_by == "admin"
        assert restored.payload == {"cmd": "eval('os.system(...)')"}


# ─── SQLiteHITLStore Tests ─────────────────────────────────

class TestSQLiteHITLStore:
    def test_save_and_get(self, hitl_store: SQLiteHITLStore):
        interrupt = HITLInterrupt(
            execution_id="exec-1",
            task_id="task-1",
            tool_name="tool.terminal",
            reason="REQUIRES_APPROVAL",
            payload={"cmd": "ls"},
        )
        hitl_store.save(interrupt)
        fetched = hitl_store.get(interrupt.id)
        assert fetched is not None
        assert fetched.execution_id == "exec-1"
        assert fetched.reason == "REQUIRES_APPROVAL"
        assert fetched.payload == {"cmd": "ls"}

    def test_survives_reopen(self, tmp_path: Path):
        eng1 = SQLiteEngine(tmp_path / "reopen_hitl.db")
        eng1.init_schema()
        store1 = SQLiteHITLStore(eng1)
        interrupt = HITLInterrupt(execution_id="exec-1", reason="test")
        store1.save(interrupt)
        eng1.close()

        eng2 = SQLiteEngine(tmp_path / "reopen_hitl.db")
        eng2.init_schema()
        store2 = SQLiteHITLStore(eng2)
        fetched = store2.get(interrupt.id)
        assert fetched is not None
        assert fetched.reason == "test"
        eng2.close()

    def test_list_pending(self, hitl_store: SQLiteHITLStore):
        # 2 pending
        i1 = HITLInterrupt(execution_id="e1", reason="r1")
        i2 = HITLInterrupt(execution_id="e2", reason="r2")
        hitl_store.save(i1)
        hitl_store.save(i2)
        # 1 approved
        i3 = HITLInterrupt(execution_id="e3", reason="r3")
        i3.transition_to(HITLStatus.APPROVED)
        hitl_store.save(i3)

        pending = hitl_store.list_pending()
        assert len(pending) == 2
        assert all(p.status == HITLStatus.PENDING for p in pending)

    def test_list_for_execution(self, hitl_store: SQLiteHITLStore):
        for i in range(3):
            hitl_store.save(HITLInterrupt(execution_id="exec-1", reason=f"r{i}"))
        hitl_store.save(HITLInterrupt(execution_id="exec-2", reason="other"))

        result = hitl_store.list_for_execution("exec-1")
        assert len(result) == 3
        assert all(r.execution_id == "exec-1" for r in result)

    def test_count(self, hitl_store: SQLiteHITLStore):
        hitl_store.save(HITLInterrupt(execution_id="e1"))
        hitl_store.save(HITLInterrupt(execution_id="e1"))
        hitl_store.save(HITLInterrupt(execution_id="e2"))
        assert hitl_store.count() == 3
        assert hitl_store.count("pending") == 3


# ─── HITLManager Tests ─────────────────────────────────────

class TestHITLManager:
    def test_crear_interrupcion(self, manager: HITLManager):
        interrupt = manager.crear_interrupcion(
            execution_id="exec-1",
            task_id="task-5",
            tool_name="tool.terminal",
            reason="REQUIRES_APPROVAL",
            payload={"cmd": "rm -rf /"},
        )
        assert interrupt.id != ""
        assert interrupt.status == HITLStatus.PENDING
        assert interrupt.checkpoint_id is None  # No checkpoint manager

    def test_aprobar(self, manager: HITLManager):
        interrupt = manager.crear_interrupcion(
            execution_id="exec-1",
            reason="test",
        )
        approved = manager.aprobar(interrupt.id, decided_by="admin", decision_reason="OK")
        assert approved.status == HITLStatus.APPROVED
        assert approved.decided_by == "admin"

    def test_denegar(self, manager: HITLManager):
        interrupt = manager.crear_interrupcion(
            execution_id="exec-1",
            reason="test",
        )
        denied = manager.denegar(interrupt.id, decided_by="admin")
        assert denied.status == HITLStatus.DENIED

    def test_reanudar(self, manager: HITLManager):
        interrupt = manager.crear_interrupcion(
            execution_id="exec-1",
            reason="test",
        )
        manager.aprobar(interrupt.id)
        result = manager.reanudar(interrupt.id)
        assert result is not None
        assert result["resumable"] is True

    def test_reanudar_without_approval_fails(self, manager: HITLManager):
        interrupt = manager.crear_interrupcion(
            execution_id="exec-1",
            reason="test",
        )
        with pytest.raises(ValueError, match="No se puede reanudar"):
            manager.reanudar(interrupt.id)

    def test_expirar(self, manager: HITLManager):
        interrupt = manager.crear_interrupcion(
            execution_id="exec-1",
            reason="test",
        )
        expired = manager.expirar(interrupt.id)
        assert expired is not None
        assert expired.status == HITLStatus.EXPIRED

    def test_aprobar_already_approved_fails(self, manager: HITLManager):
        interrupt = manager.crear_interrupcion(
            execution_id="exec-1",
            reason="test",
        )
        manager.aprobar(interrupt.id)
        with pytest.raises(ValueError, match="Transición inválida"):
            manager.aprobar(interrupt.id)

    def test_obtener_inexistente(self, manager: HITLManager):
        assert manager.obtener_interrupcion("nonexistent") is None

    def test_reanudar_inexistente(self, manager: HITLManager):
        assert manager.reanudar("nonexistent") is None
