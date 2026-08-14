"""
Tests del runtime asíncrono (Fase 2).

Criterios de aceptación del roadmap:
- 10 Tasks independientes se completan en ~T (no en ~10T)
- Task con timeout=10s que tarda 60s → FAILED en ~10s
- Task que falla 2 veces y tiene éxito la 3ra → COMPLETED tras 3 intentos
- EventBus async: compatibilidad hacia atrás
- TaskQueue: persistencia (create → close → reopen → verify)
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from eon.event_bus_async import AsyncEventBus, SyncEventBusAdapter
from eon.persistence import SQLiteEngine
from eon.workers.task_queue import SQLiteTaskQueue, TaskEntry, TaskQueueState
from eon.workers.worker_pool import WorkerPool

# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def engine(tmp_path: Path) -> SQLiteEngine:
    eng = SQLiteEngine(tmp_path / "test_async.db")
    eng.init_schema()
    yield eng
    eng.close()


@pytest.fixture
def task_queue(engine: SQLiteEngine) -> SQLiteTaskQueue:
    return SQLiteTaskQueue(engine)


# ─── AsyncEventBus Tests ──────────────────────────────────

class TestAsyncEventBus:
    @pytest.mark.asyncio
    async def test_emit_non_blocking(self):
        """emit() retorna antes de que el handler termine."""
        bus = AsyncEventBus()
        handler_completed = asyncio.Event()

        async def slow_handler(**data):
            await asyncio.sleep(0.1)
            handler_completed.set()

        bus.subscribe("event", slow_handler)

        # emit debe retornar inmediatamente
        start = time.monotonic()
        await bus.emit("event", key="value")
        elapsed = time.monotonic() - start
        assert elapsed < 0.05  # no esperó al handler

        # drain procesa el handler
        await bus.drain()
        assert handler_completed.is_set()

    @pytest.mark.asyncio
    async def test_emit_and_wait(self):
        """emit_and_wait() espera a que el handler termine."""
        bus = AsyncEventBus()
        received = []

        async def handler(**data):
            received.append(data)

        bus.subscribe("test", handler)
        await bus.emit_and_wait("test", value=42)
        assert len(received) == 1
        assert received[0]["value"] == 42

    @pytest.mark.asyncio
    async def test_sync_handler_compatible(self):
        """Los handlers síncronos funcionan en el bus async."""
        bus = AsyncEventBus()
        received = []

        def sync_handler(**data):
            received.append(data["msg"])

        bus.subscribe("sync", sync_handler)
        await bus.emit_and_wait("sync", msg="hello")
        assert received == ["hello"]

    @pytest.mark.asyncio
    async def test_multiple_handlers(self):
        bus = AsyncEventBus()
        results = []

        async def h1(**data):
            results.append("h1")

        async def h2(**data):
            results.append("h2")

        bus.subscribe("multi", h1)
        bus.subscribe("multi", h2)
        await bus.emit_and_wait("multi", x=1)
        assert set(results) == {"h1", "h2"}

    @pytest.mark.asyncio
    async def test_handler_exception_isolated(self):
        """Un handler que falla no rompe otros handlers."""
        bus = AsyncEventBus()
        success = []

        async def bad_handler(**data):
            raise ValueError("boom")

        async def good_handler(**data):
            success.append(True)

        bus.subscribe("err", bad_handler)
        bus.subscribe("err", good_handler)
        await bus.emit_and_wait("err", x=1)
        assert len(success) == 1  # el buen handler se ejecutó


# ─── SyncEventBusAdapter Tests ────────────────────────────

class TestSyncEventBusAdapter:
    def test_sync_emit(self):
        adapter = SyncEventBusAdapter()
        received = []
        adapter.subscribe("event", lambda **d: received.append(d))
        adapter.emit("event", key="value")
        assert len(received) == 1
        assert received[0]["key"] == "value"

    def test_compatible_with_event_bus_interface(self):
        adapter = SyncEventBusAdapter()
        # Mismas métodos que EventBus
        assert hasattr(adapter, "subscribe")
        assert hasattr(adapter, "on")
        assert hasattr(adapter, "emit")


# ─── TaskQueue Tests ─────────────────────────────────────

class TestSQLiteTaskQueue:
    def test_enqueue_and_get(self, task_queue):
        entry = TaskEntry(task_id="t1", capability_id="code.write", payload={"x": 1})
        assert task_queue.enqueue(entry) is True
        fetched = task_queue.get("t1")
        assert fetched is not None
        assert fetched.capability_id == "code.write"
        assert fetched.estado == TaskQueueState.PENDING

    def test_idempotency(self, task_queue):
        entry = TaskEntry(task_id="t1", capability_id="x")
        assert task_queue.enqueue(entry) is True
        # Segundo enqueue se ignora
        assert task_queue.enqueue(entry) is False

    def test_lease(self, task_queue):
        task_queue.enqueue(TaskEntry(task_id="t1", capability_id="x"))
        entry = task_queue.lease(worker_id="w1", timeout_seconds=30)
        assert entry is not None
        assert entry.estado == TaskQueueState.RUNNING
        assert entry.leased_by == "w1"
        assert entry.attempts == 1

    def test_lease_by_capability(self, task_queue):
        task_queue.enqueue(TaskEntry(task_id="t1", capability_id="alpha"))
        task_queue.enqueue(TaskEntry(task_id="t2", capability_id="beta"))
        entry = task_queue.lease(worker_id="w1", capability_id="beta")
        assert entry is not None
        assert entry.task_id == "t2"

    def test_complete(self, task_queue):
        task_queue.enqueue(TaskEntry(task_id="t1", capability_id="x"))
        task_queue.lease(worker_id="w1")
        assert task_queue.complete("t1", result={"ok": True}) is True
        entry = task_queue.get("t1")
        assert entry.estado == TaskQueueState.COMPLETED
        assert entry.result == {"ok": True}

    def test_fail_with_retry(self, task_queue):
        entry = TaskEntry(task_id="t1", capability_id="x", max_retries=2)
        task_queue.enqueue(entry)
        task_queue.lease(worker_id="w1")
        task_queue.fail("t1", error="boom", backoff_base=0.0)
        fetched = task_queue.get("t1")
        assert fetched.estado == TaskQueueState.PENDING  # reintenta
        assert fetched.attempts == 1
        assert fetched.last_error == "boom"

    def test_fail_exhausted(self, task_queue):
        entry = TaskEntry(task_id="t1", capability_id="x", max_retries=1)
        task_queue.enqueue(entry)
        # Intento 1
        task_queue.lease(worker_id="w1")
        task_queue.fail("t1", error="err1", backoff_base=0.0)
        # Intento 2
        entry2 = task_queue.get("t1")
        assert entry2.estado == TaskQueueState.PENDING
        task_queue.lease(worker_id="w1")
        task_queue.fail("t1", error="err2", backoff_base=0.0)
        # Debe estar FAILED (max_retries=1, attempts=2)
        final = task_queue.get("t1")
        assert final.estado == TaskQueueState.FAILED

    def test_cancel(self, task_queue):
        task_queue.enqueue(TaskEntry(task_id="t1", capability_id="x"))
        task_queue.cancel("t1")
        assert task_queue.get("t1").estado == TaskQueueState.CANCELLED

    def test_count(self, task_queue):
        task_queue.enqueue(TaskEntry(task_id="t1", capability_id="x"))
        task_queue.enqueue(TaskEntry(task_id="t2", capability_id="y"))
        assert task_queue.count() == 2
        assert task_queue.count(TaskQueueState.PENDING) == 2
        task_queue.lease(worker_id="w1")
        assert task_queue.count(TaskQueueState.PENDING) == 1
        assert task_queue.count(TaskQueueState.RUNNING) == 1

    def test_survives_reopen(self, tmp_path: Path):
        eng1 = SQLiteEngine(tmp_path / "reopen.db")
        eng1.init_schema()
        q1 = SQLiteTaskQueue(eng1)
        q1.enqueue(TaskEntry(task_id="t1", capability_id="x"))
        eng1.close()

        eng2 = SQLiteEngine(tmp_path / "reopen.db")
        eng2.init_schema()
        q2 = SQLiteTaskQueue(eng2)
        entry = q2.get("t1")
        assert entry is not None
        assert entry.capability_id == "x"
        eng2.close()


# ─── WorkerPool Tests ─────────────────────────────────────

class TestWorkerPool:
    @pytest.mark.asyncio
    async def test_concurrent_execution(self, task_queue):
        """10 Tasks independientes se completan en ~T, no ~10T."""
        # Cada task duerme 0.1s
        import time

        def slow_executor(cap_id: str, params: dict) -> bool:
            time.sleep(0.1)
            return True

        for i in range(10):
            task_queue.enqueue(
                TaskEntry(
                    task_id=f"t{i}",
                    capability_id="work",
                    timeout_seconds=10.0,
                )
            )

        pool = WorkerPool(
            queue=task_queue,
            executor=slow_executor,
            max_workers=10,
        )

        start = time.monotonic()
        await pool.start()
        await pool.wait_until_empty(timeout=30.0)
        await pool.stop()
        elapsed = time.monotonic() - start

        # 10 tasks de 0.1s con 10 workers → ~0.1-0.3s, no ~1s
        assert elapsed < 0.6, f"Expected <0.6s, got {elapsed:.2f}s"
        assert task_queue.count(TaskQueueState.COMPLETED) == 10

    @pytest.mark.asyncio
    async def test_timeout(self, task_queue):
        """Task con timeout=0.1s que tarda 1s → FAILED en ~0.1s."""
        import time

        def slow_executor(cap_id: str, params: dict) -> bool:
            time.sleep(1.0)
            return True

        task_queue.enqueue(
            TaskEntry(
                task_id="t-timeout",
                capability_id="slow",
                timeout_seconds=0.1,
                max_retries=0,
            )
        )

        pool = WorkerPool(
            queue=task_queue,
            executor=slow_executor,
            max_workers=1,
        )

        start = time.monotonic()
        await pool.start()
        await pool.wait_until_empty(timeout=10.0)
        await pool.stop()
        elapsed = time.monotonic() - start

        # Debe fallar en ~0.1s, no ~1s
        assert elapsed < 0.5, f"Expected <0.5s, got {elapsed:.2f}s"
        entry = task_queue.get("t-timeout")
        assert entry.estado == TaskQueueState.FAILED
        assert "Timeout" in (entry.last_error or "")

    @pytest.mark.asyncio
    async def test_retry_then_success(self, task_queue):
        """Task que falla 2 veces y tiene éxito la 3ra → COMPLETED."""
        call_count = {"n": 0}

        def flaky_executor(cap_id: str, params: dict) -> bool:
            call_count["n"] += 1
            if call_count["n"] < 3:
                return False  # falla intentos 1 y 2
            return True  # éxito en intento 3

        task_queue.enqueue(
            TaskEntry(
                task_id="t-retry",
                capability_id="flaky",
                max_retries=2,
                timeout_seconds=5.0,
            )
        )

        pool = WorkerPool(
            queue=task_queue,
            executor=flaky_executor,
            max_workers=1,
            backoff_base=0.01,
        )

        await pool.start()
        await pool.wait_until_empty(timeout=30.0)
        await pool.stop()

        assert call_count["n"] == 3
        entry = task_queue.get("t-retry")
        assert entry.estado == TaskQueueState.COMPLETED
        assert entry.attempts == 3

    @pytest.mark.asyncio
    async def test_capability_semaphore(self, task_queue):
        """El semáforo por capability limita concurrencia."""
        import time

        concurrent = {"current": 0, "max": 0}

        def tracking_executor(cap_id: str, params: dict) -> bool:
            concurrent["current"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["current"])
            time.sleep(0.05)
            concurrent["current"] -= 1
            return True

        for i in range(6):
            task_queue.enqueue(
                TaskEntry(
                    task_id=f"t-sem-{i}",
                    capability_id="limited",
                    timeout_seconds=5.0,
                )
            )

        pool = WorkerPool(
            queue=task_queue,
            executor=tracking_executor,
            max_workers=6,  # muchos workers
        )
        pool.set_capability_concurrency("limited", 2)  # pero max 2 concurrent

        await pool.start()
        await pool.wait_until_empty(timeout=30.0)
        await pool.stop()

        assert concurrent["max"] <= 2, f"Expected max 2 concurrent, got {concurrent['max']}"
        assert task_queue.count(TaskQueueState.COMPLETED) == 6

    @pytest.mark.asyncio
    async def test_events_emitted(self, task_queue):
        """El pool emite TASK_COMPLETADA al EventBus."""
        from eon.event_bus import EventBus

        bus = EventBus()
        events_received = []
        bus.subscribe("TASK_COMPLETADA", lambda **d: events_received.append(d))

        task_queue.enqueue(
            TaskEntry(task_id="t-evt", capability_id="work", timeout_seconds=5.0)
        )

        pool = WorkerPool(
            queue=task_queue,
            event_bus=bus,
            executor=lambda cap, params: True,
            max_workers=1,
        )

        await pool.start()
        await pool.wait_until_empty(timeout=10.0)
        await pool.stop()

        assert len(events_received) == 1
        assert events_received[0]["task_id"] == "t-evt"
