"""
eon.workers.worker_pool
==========================
WorkerPool — pool de workers concurrentes con asyncio.

Features:
- N workers configurables (default: cpu_count)
- `asyncio.Semaphore` por capability para límite de concurrencia
- Reintentos automáticos con backoff exponencial
- Timeouts por Task via `asyncio.wait_for()`
- Integración con SQLiteTaskQueue (durable)
- Integración con EventBus (emite TASK_COMPLETADA / TASK_FALLIDA)

Uso:
    pool = WorkerPool(
        queue=task_queue,
        event_bus=event_bus,
        executor=my_capability_executor,
        max_workers=4,
    )
    await pool.start()
    # ... las tasks se procesan concurrentemente
    await pool.stop()
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from typing import Any

from .task_queue import SQLiteTaskQueue, TaskQueueState

logger = logging.getLogger("eon.workers.worker_pool")

# Tipo del executor de capabilities: (capability_id, parametros) -> bool
CapabilityExecutor = Callable[[str, dict], bool]
AsyncCapabilityExecutor = Callable[[str, dict], Any]  # devuelve awaitable


class WorkerPool:
    """Pool de workers async que procesan tasks de una TaskQueue.

    Cada worker es un task async que:
    1. Hace lease de una Task PENDING del queue
    2. La ejecuta con timeout y concurrency limit
    3. Marca complete o fail (con retry si quedan intentos)
    4. Repite

    El pool emite eventos al EventBus:
    - TASK_COMPLETADA: task ejecutada con éxito
    - TASK_FALLIDA: task falló (y no le quedan reintentos)
    """

    def __init__(
        self,
        queue: SQLiteTaskQueue,
        event_bus: Any | None = None,
        executor: CapabilityExecutor | None = None,
        async_executor: AsyncCapabilityExecutor | None = None,
        max_workers: int | None = None,
        backoff_base: float = 0.1,
        backoff_max: float = 10.0,
        poll_interval: float = 0.05,
    ) -> None:
        self._queue = queue
        self._event_bus = event_bus
        self._executor = executor
        self._async_executor = async_executor
        self._max_workers = max_workers or os.cpu_count() or 4
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._poll_interval = poll_interval

        # Semáforo por capability para limitar concurrencia
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._semaphore_default = 0  # 0 = sin límite por capability
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._stop_event = asyncio.Event()

    def set_capability_concurrency(self, capability_id: str, max_concurrent: int) -> None:
        """Configura el límite de concurrencia para una capability."""
        self._semaphores[capability_id] = asyncio.Semaphore(max_concurrent)

    def _get_semaphore(self, capability_id: str) -> asyncio.Semaphore | None:
        return self._semaphores.get(capability_id)

    async def start(self) -> None:
        """Inicia los workers del pool."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        for i in range(self._max_workers):
            worker = asyncio.create_task(self._worker_loop(f"pool-worker-{i}"))
            self._workers.append(worker)
        logger.info("WorkerPool iniciado con %d workers", self._max_workers)

    async def stop(self) -> None:
        """Detiene todos los workers del pool."""
        self._running = False
        self._stop_event.set()
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("WorkerPool detenido")

    async def _worker_loop(self, worker_id: str) -> None:
        """Loop principal de cada worker: lease → execute → complete/fail."""
        while self._running:
            try:
                # Recuperar leases expirados
                self._queue.recover_expired_leases()

                # Hacer lease de una task
                entry = self._queue.lease(worker_id=worker_id)
                if entry is None:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=self._poll_interval
                        )
                    continue

                # Ejecutar con concurrency limit
                sem = self._get_semaphore(entry.capability_id)
                if sem is not None:
                    async with sem:
                        await self._execute_task(worker_id, entry)
                else:
                    await self._execute_task(worker_id, entry)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error en worker %s", worker_id)

    async def _execute_task(self, worker_id: str, entry: Any) -> None:
        """Ejecuta una Task con timeout y registra el resultado."""
        task_id = entry.task_id
        capability_id = entry.capability_id
        payload = entry.payload
        timeout = entry.timeout_seconds

        success = False
        error_msg: str | None = None

        try:
            if self._async_executor is not None:
                # Executor async nativo
                try:
                    result = await asyncio.wait_for(
                        self._async_executor(capability_id, payload),
                        timeout=timeout,
                    )
                    success = bool(result)
                except TimeoutError:
                    success = False
                    error_msg = f"Timeout tras {timeout}s"
            elif self._executor is not None:
                # Executor síncrono: ejecutar en thread para no bloquear el loop
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(self._executor, capability_id, payload),
                        timeout=timeout,
                    )
                    success = bool(result)
                except TimeoutError:
                    success = False
                    error_msg = f"Timeout tras {timeout}s"
            else:
                # Sin executor: éxito por defecto (testing)
                success = True

        except Exception as exc:
            success = False
            error_msg = str(exc)

        # Registrar resultado
        if success:
            self._queue.complete(task_id, result={"ok": True, "worker_id": worker_id})
            if self._event_bus is not None:
                self._emit("TASK_COMPLETADA", task_id=task_id, worker_id=worker_id)
            logger.debug("Task %s completada por %s", task_id, worker_id)
        else:
            self._queue.fail(
                task_id,
                error=error_msg or "Error desconocido",
                backoff_base=self._backoff_base,
                backoff_max=self._backoff_max,
            )
            # Verificar si la task sigue reintentable
            entry_after = self._queue.get(task_id)
            if entry_after is not None and entry_after.estado.es_terminal:
                # No más reintentos: emitir TASK_FALLIDA
                if self._event_bus is not None:
                    self._emit("TASK_FALLIDA", task_id=task_id, worker_id=worker_id, error=error_msg)
                logger.warning("Task %s falló definitivamente: %s", task_id, error_msg)
            else:
                logger.info("Task %s falló, reintentando (intento %d)", task_id, entry.attempts)

    def _emit(self, event_name: str, **data: Any) -> None:
        """Emite un evento al EventBus (sync o async)."""
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(event_name, **data)
        except Exception:
            logger.exception("Error emitiendo evento %s", event_name)

    async def wait_until_empty(self, timeout: float = 60.0) -> bool:
        """Espera hasta que no haya tasks PENDING ni RUNNING.

        Returns:
            True si la cola está vacía, False si timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            pending = self._queue.count(TaskQueueState.PENDING)
            running = self._queue.count(TaskQueueState.RUNNING)
            if pending == 0 and running == 0:
                return True
            await asyncio.sleep(0.1)
        return False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def max_workers(self) -> int:
        return self._max_workers
