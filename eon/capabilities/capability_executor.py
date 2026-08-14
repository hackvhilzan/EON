"""
eon.capabilities.capability_executor
=======================================
Construye el `Callable[[str, dict], bool]` (`EjecutorDeCapability` de
`eon.workers.executor`) que se inyecta como `KernelRuntime(task_executor=...)`
(`eon/runtime.py`). Es el único punto real de conexión entre el Kernel y
`eon.tools`/`eon.llm` -- ver `eon/CAPABILITIES.md` para el contrato completo
y `AUDITORIA_TOOLS_LLM_INTEGRACION.md` para el razonamiento detrás de cada
decisión de este módulo.

Es capa de composición, no de dominio: no lo importa nada de `eon.workers`,
`eon.scheduler`, `eon.planner`, `eon.coordinator` -- la dependencia va en un
solo sentido, de aquí hacia ellos (nunca al revés). Traduce entre:

  - el contrato síncrono y `bool`-only del Kernel (WORKERS.md, congelado
    v1.0: `EjecutorDeCapability = Callable[[str, dict], bool]`, sin
    `task_id`/`worker_id`, sin canal para el `data` del resultado), y
  - la interfaz async con resultado enriquecido (`ToolResult`) de
    `eon.tools.registry.ToolRegistry`.

Decisión tomada (Opción A de AUDITORIA_TOOLS_LLM_INTEGRACION.md §5.3): el
contrato `EjecutorDeCapability` NO se toca. `ultimo_resultado` expone el
`ToolResult` completo de la última invocación como mecanismo de solo lectura
para quien construya/observe el executor (logging, un `WorkspaceManager`
que decida persistirlo como artefacto por fuera de este módulo, etc.) --
pero ese valor nunca viaja de vuelta al Kernel a través del `bool` que ve
`TaskExecutor`. Si en el futuro se necesita que el propio Kernel transporte
`data`, es una decisión de versionar WORKERS.md (Opción B), no algo que este
módulo deba forzar por su cuenta.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping

from eon.llm.base import LLM
from eon.llm.provider import get_provider_lazy
from eon.tools.base_tool import ToolResult
from eon.tools.registry import ToolRegistry

from .capability_map import CapabilityNotMappedError, CapabilitySpec, resolver

logger = logging.getLogger("eon.capabilities")


class CapabilityExecutor:
    """Callable compatible con `eon.workers.executor.EjecutorDeCapability`.

    Uso:
        executor = CapabilityExecutor()
        runtime = KernelRuntime(task_executor=executor, ...)
        ...
        executor.cerrar()  # al terminar el proceso (o el test)

    O como context manager:
        with CapabilityExecutor() as executor:
            runtime = KernelRuntime(task_executor=executor, ...)
            ...
    """

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        llm: LLM | None = None,
        capability_map: Mapping[str, CapabilitySpec] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        # ToolRegistry propio si no se inyecta uno: autodiscover() ya tolera
        # dependencias opcionales ausentes (httpx, pypdf...) sin tumbar el
        # registro completo (eon/tools/registry.py) -- ver Hallazgo 1 de la
        # auditoría sobre `MemoryTool` en particular, que hoy queda sin
        # registrar por su import muerto a `eon.core`.
        self._registry = tool_registry or ToolRegistry().autodiscover()
        # LLM perezoso: no exige API key/SDK instalado hasta el primer
        # generate() real (eon.llm.provider.get_provider_lazy).
        self._llm = llm or get_provider_lazy()
        self._capability_map = capability_map
        self._timeout = timeout_seconds
        self.ultimo_resultado: ToolResult | None = None

        # Puente síncrono -> asíncrono: un único event loop dedicado en un
        # hilo propio, creado una vez (no uno por llamada). Cada invocación
        # usa asyncio.run_coroutine_threadsafe(...).result(timeout=...) desde
        # el callable síncrono -- evita tanto el coste de crear/destruir un
        # loop en cada Task como el error de intentar asyncio.run() dentro de
        # un loop ya activo (relevante el día que exista una interfaz async
        # -- p. ej. FastAPI -- corriendo en el mismo proceso).
        self._loop = asyncio.new_event_loop()
        self._hilo = threading.Thread(target=self._loop.run_forever, name="eon-capabilities-loop", daemon=True)
        self._hilo.start()
        self._cerrado = False

    def __call__(self, capability_id: str, parametros: dict) -> bool:
        """Firma exacta de `EjecutorDeCapability` (`eon.workers.executor`).
        Esto es lo que se pasa como `KernelRuntime(task_executor=<esta instancia>)`.

        Nunca deja escapar una excepción: capability_id no mapeado, timeout
        del puente, o fallo de la Tool en sí, todos se traducen a `False` --
        el detalle queda en `self.ultimo_resultado` y en el log, nunca en una
        excepción que suba hasta `TaskExecutor`/`Dispatcher` (que no la
        esperan; ver WORKERS.md Invariante 9, "sin ejecuciones silenciosas"
        pero tampoco excepciones que escapen del executor inyectado)."""
        if self._cerrado:
            logger.error("CapabilityExecutor ya fue cerrado; no se pueden despachar más capabilities.")
            self.ultimo_resultado = ToolResult(ok=False, error="CapabilityExecutor cerrado.")
            return False

        try:
            spec = resolver(capability_id, self._capability_map)
        except CapabilityNotMappedError:
            logger.error("capability_id=%s no está mapeado a ninguna Tool.", capability_id)
            self.ultimo_resultado = ToolResult(ok=False, error=f"capability_id no mapeado: {capability_id!r}")
            return False

        kwargs = spec.parametros_mapper(parametros or {})
        futuro = asyncio.run_coroutine_threadsafe(self._registry.execute(spec.tool_name, **kwargs), self._loop)
        try:
            resultado = futuro.result(timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 -- timeout u otro fallo del puente en sí, no de la Tool
            logger.error(
                "capability_id=%s tool=%s -> excepción del puente sync/async: %s", capability_id, spec.tool_name, exc
            )
            self.ultimo_resultado = ToolResult(ok=False, error=str(exc))
            return False

        self.ultimo_resultado = resultado
        if not resultado.ok:
            logger.warning("capability_id=%s tool=%s -> fallo: %s", capability_id, spec.tool_name, resultado.error)
        return resultado.ok

    def generar(self, prompt: str) -> str:
        """Acceso directo al LLM configurado, para capabilities que razonan
        en vez de actuar. No pasa por `ToolRegistry` (no hay una Tool `llm`
        en `eon.tools` hoy) ni por el puente async: `LLM.generate` ya es
        síncrono (`eon.llm.base.LLM`), así que encaja directamente en la
        cadena síncrona del Kernel sin necesidad de puente. No se invoca
        automáticamente desde `__call__`; queda disponible para que un
        `capability_map` con un `parametros_mapper` a medida lo use, o para
        una futura `LLMTool` que envuelva esto con la interfaz `Tool.execute`
        async (ver `eon/CAPABILITIES.md` §5, pendiente)."""
        return self._llm.generate(prompt)

    def cerrar(self) -> None:
        """Apaga el hilo del loop dedicado y cierra las Tools. Llamar al
        finalizar el proceso (o el test) que construyó este executor -- si
        no se llama, el hilo daemon no impide que el proceso termine, pero
        queda corriendo hasta entonces."""
        if self._cerrado:
            return
        self._cerrado = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._hilo.join(timeout=5)
        self._loop.close()
        # Cerrar Tools con recursos (httpx clients, etc.)
        if self._registry is not None:
            self._registry.close_all()

    def __enter__(self) -> CapabilityExecutor:
        return self

    def __exit__(self, *exc_info) -> None:
        self.cerrar()
