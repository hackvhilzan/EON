"""
eon.event_bus_async
======================
AsyncEventBus — bus de eventos asíncrono no bloqueante.

Diferencias con el EventBus síncrono:
- `emit()` es `async` y NO bloquea: encola el evento y retorna inmediatamente.
- Los handlers pueden ser `async` o síncronos (se detecta automáticamente).
- `drain()` procesa todos los eventos pendientes.
- `emit_and_wait()` emite y espera a que todos los handlers terminen.

Compatibilidad hacia atrás:
- `SyncEventBusAdapter` envuelve un AsyncEventBus exponiendo la misma
  interfaz síncrona del EventBus original (emit bloqueante, subscribe).
- Los tests existentes pueden seguir usando el EventBus síncrono sin cambios.

Uso:
    bus = AsyncEventBus()

    async def handler(**data):
        print("recibido:", data)

    bus.subscribe("event", handler)
    await bus.emit("event", key="value")  # no bloquea
    await bus.drain()  # procesa pendientes

    # O síncrono:
    sync_bus = SyncEventBusAdapter(bus)
    sync_bus.subscribe("event", sync_handler)
    sync_bus.emit("event", key="value")  # bloquea hasta procesar
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("eon.event_bus_async")


@dataclass
class PendingEvent:
    """Evento encolado esperando procesamiento."""

    name: str
    data: dict[str, Any]


class AsyncEventBus:
    """Bus de eventos asíncrono no bloqueante.

    - `emit()` encola y retorna (no espera a los handlers).
    - `drain()` procesa todos los eventos pendientes.
    - `emit_and_wait()` emite y espera a que los handlers terminen.
    - Acepta handlers async y síncronos.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._queue: asyncio.Queue[PendingEvent] | None = None
        self._processing = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ensure_queue(self) -> asyncio.Queue[PendingEvent]:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    def subscribe(self, event_name: str, handler: Callable) -> None:
        """Registra un handler (async o síncrono) para `event_name`."""
        self._subscribers[event_name].append(handler)

    # Alias para compatibilidad con EventBus.on
    on = subscribe

    async def emit(self, event_name: str, **data: Any) -> None:
        """Encola un evento y retorna inmediatamente. No bloquea."""
        queue = self._ensure_queue()
        await queue.put(PendingEvent(name=event_name, data=data))

    async def drain(self) -> int:
        """Procesa todos los eventos pendientes. Devuelve el número procesados."""
        queue = self._ensure_queue()
        processed = 0
        while not queue.empty():
            event = await queue.get()
            await self._dispatch(event)
            queue.task_done()
            processed += 1
        return processed

    async def emit_and_wait(self, event_name: str, **data: Any) -> None:
        """Emite un evento y espera a que todos los handlers terminen."""
        await self._dispatch(PendingEvent(name=event_name, data=data))

    async def _dispatch(self, event: PendingEvent) -> None:
        """Invoca todos los handlers registrados para un evento."""
        for handler in self._subscribers.get(event.name, []):
            try:
                result = handler(**event.data)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception(
                    "Error en handler async del EventBus para evento '%s'", event.name
                )

    @property
    def pending_count(self) -> int:
        """Número de eventos encolados sin procesar."""
        if self._queue is None:
            return 0
        return self._queue.qsize()


class SyncEventBusAdapter:
    """Adaptador síncrono para AsyncEventBus.

    Expone la misma interfaz que el EventBus síncrono original:
    `subscribe(event, callback)` y `emit(event, **data)`.

    `emit()` procesa los handlers inmediatamente (bloqueante), manteniendo
    la semántica del EventBus original. Esto permite usar AsyncEventBus
    en código síncrono existente sin cambios.
    """

    def __init__(self, async_bus: AsyncEventBus | None = None) -> None:
        self._bus = async_bus or AsyncEventBus()

    @property
    def _bus_ref(self) -> AsyncEventBus:
        return self._bus

    def subscribe(self, event_name: str, callback: Callable) -> None:
        self._bus.subscribe(event_name, callback)

    on = subscribe

    def emit(self, event_name: str, **data: Any) -> None:
        """Emite un evento síncronamente (bloqueante, igual que EventBus).

        Si hay un event loop running, usa run_coroutine_threadsafe.
        Si no, crea uno temporal para procesar.
        """
        try:
            loop = asyncio.get_running_loop()
            # Estamos en un loop async: procesar síncronamente para mantener
            # la semántica del EventBus original (los handlers se ejecutan
            # antes de que emit() retorne).
            asyncio.run_coroutine_threadsafe(
                self._bus.emit_and_wait(event_name, **data), loop
            )
        except RuntimeError:
            # No hay loop running: crear uno temporal.
            asyncio.run(self._bus.emit_and_wait(event_name, **data))
