# CAPABILITIES.md — Contrato del punto de integración Tools/LLM (Fase 14)

**Estado: implementado (v1.0).**

Documenta `eon/capabilities/`, la capa de composición que conecta el Kernel
(`eon/runtime.py::KernelRuntime`) con `eon.tools`/`eon.llm`. Auditoría previa:
`eon/docs/AUDITORIA_TOOLS_LLM_INTEGRACION.md` — este documento fija las
decisiones que ahí quedaron abiertas.

## 1. Dónde vive y por qué

`eon/capabilities/` es un paquete nuevo, al mismo nivel que `tools/` y `llm/`,
pero de naturaleza distinta: no es un dominio del Kernel (no tiene
Store/Manager/eventos propios) ni es una "capacidad" en el sentido de
`tools/`/`llm/`. Cumple el mismo rol que los adapters de `eon/runtime.py`
(`PlannerPortAdapter`, `SchedulerPortAdapter`, etc.): traduce entre dos
contratos ya cerrados sin que ninguno de los dos lados se entere del otro.

- `eon.capabilities` importa `eon.tools` y `eon.llm`.
- `eon.capabilities` **no** importa `eon.workers`, `eon.scheduler`,
  `eon.planner`, `eon.coordinator` — no necesita hacerlo: solo construye un
  `Callable[[str, dict], bool]` con la firma exacta que
  `eon.workers.executor.EjecutorDeCapability` ya esperaba desde Fase 10.
- `eon.workers`/`eon.runtime` no importan `eon.capabilities`: quien la usa es
  el composition root (`eon/__main__.py` con `--use-tools`, o cualquier otro
  llamador de `KernelRuntime`), nunca el Kernel por su cuenta. Esto respeta
  MAPA_ARQUITECTURA_KERNEL.md §4.1 ("Coordinator depende directamente de LLM
  o Tools para gobernar el flujo" — ilegítimo).

## 2. Contrato de `CapabilityExecutor`

`eon.capabilities.CapabilityExecutor` es un callable:

```python
executor = CapabilityExecutor()
runtime = KernelRuntime(task_executor=executor, ...)
...
executor.cerrar()
```

- `executor(capability_id: str, parametros: dict) -> bool` — firma idéntica a
  `EjecutorDeCapability`. Nunca lanza una excepción hacia el llamador
  (`TaskExecutor`/`Dispatcher` no la esperan): capability no mapeada, timeout
  del puente, o fallo real de la Tool, todo se traduce a `False`.
- `executor.ultimo_resultado` — el `ToolResult` (`ok`/`data`/`error`/
  `duration_seconds`) completo de la última invocación. Es de solo lectura y
  **nunca** viaja de vuelta al Kernel — ver §3.
- `executor.generar(prompt: str) -> str` — acceso directo al LLM configurado
  (`eon.llm.provider.get_provider_lazy()`), para capabilities que razonan en
  vez de actuar. No se invoca automáticamente desde `__call__`.
- `executor.cerrar()` — apaga el hilo del loop dedicado (idempotente). También
  disponible como context manager (`with CapabilityExecutor() as executor:`).

## 3. Decisión: el `data` de la Tool no viaja por el Kernel (Opción A)

`EjecutorDeCapability` sigue siendo `Callable[[str, dict], bool]`, sin
cambios en `eon/workers/executor.py` ni `eon/workers/dispatcher.py`, y sin
`task_id`/`worker_id` disponibles dentro de `CapabilityExecutor.__call__`.
Es la Opción A de `AUDITORIA_TOOLS_LLM_INTEGRACION.md` §5.3: se respeta el
contrato de WORKERS.md ("congelado v1.0") tal cual, en vez de versionarlo.

Consecuencia explícita: si una capability produce datos que alguien necesita
leer después (resultados de una búsqueda, texto generado por el LLM), esos
datos **no llegan** a Workspace/Package a través del Kernel — solo el
`ok`/`error` boolean llega, vía `task_completada`/`task_fallida`. Dos formas
legítimas de que el dato sobreviva, ninguna implementada aquí:

1. La propia Task ya trae en `parametros` el destino donde la Tool debe
   persistir su resultado (p. ej. `{"action": "write", "path": "resultados/..."}`
   para `FilesystemTool`) — la correlación con la Task la resuelve quien
   genera la Task (Planner/TaskGeneration, que sí conoce el `task_id` en
   tiempo de creación), no `CapabilityExecutor`.
2. Quien construye `CapabilityExecutor` inspecciona `executor.ultimo_resultado`
   *fuera* de la llamada a `runtime.run(...)` — por ejemplo, un llamador que
   solo dispatchea una Task a la vez y le interesa su resultado directo, sin
   pasar por Scheduler/Workers para varias Tasks concurrentes.

Si en el futuro esto resulta demasiado limitante, la vía correcta es abrir
una Fase de extensión de WORKERS.md (Opción B: `EjecutorDeCapability` recibe
`task_id`/devuelve algo más rico que `bool`), con su propio proceso de
decisión — no forzarlo desde `eon.capabilities`.

## 4. `capability_map.py` — convención de nombres

`capability_id` sigue el patrón `"tool.<nombre_de_tool>"`
(`"tool.filesystem"`, `"tool.internet"`, `"tool.api"`, `"tool.calendar"`,
`"tool.email"`). `Task.parametros` se pasa tal cual como kwargs de
`Tool.execute()` salvo que la entrada del mapa declare un
`parametros_mapper` a medida.

**`tool.terminal` y `tool.python` NO están en `CAPABILITY_MAP` por
defecto.** Son las dos Tools de mayor riesgo del set (shell arbitrario / exec
arbitrario) y hoy no hay ningún Verifier real gobernando qué capability se
aprueba completar un Objective (`KERNEL_CONSOLIDATION_REPORT.md` §5: el
Verifier de dominio real está desconectado de `KernelRuntime`, que usa
`SimpleVerifier`, un stub que solo mira `evidence["ok"]`). Habilitarlas es
una decisión explícita de quien construya el executor, pasando un
`capability_map` propio a `CapabilityExecutor(capability_map=...)` — no el
valor por defecto de este módulo.

## 5. Puente síncrono → asíncrono

Todo el Kernel (`EventBus.emit` → `SchedulerManager` → `Dispatcher` →
`TaskExecutor.ejecutar` → la función inyectada) corre síncrono, en una sola
pila de llamadas. `Tool.execute`/`ToolRegistry.execute` son `async def`.

`CapabilityExecutor` resuelve esto con un único event loop de asyncio
corriendo en un hilo dedicado, creado una vez en `__init__` (no uno por
llamada). Cada invocación hace
`asyncio.run_coroutine_threadsafe(registry.execute(...), loop).result(timeout=...)`
desde el callable síncrono — evita el error de llamar `asyncio.run()` dentro
de un loop ya activo, relevante el día que exista una interfaz async (p. ej.
FastAPI) corriendo en el mismo proceso (`ESTADO_ACTUAL.md`: hoy no existe
ninguna interfaz activa, pero es la superficie más probable a futuro).

`LLM.generate()` ya es síncrono (`eon.llm.base.LLM`) y no necesita puente;
`CapabilityExecutor.generar()` lo llama directamente.

## 6. Qué queda pendiente (no resuelto por esta fase)

- **`tool.terminal`/`tool.python`** quedan fuera del mapa por defecto (§4)
  como postura conservadora, no por falta de verificación real (ver nota
  siguiente).
- **Opción B** (§3) — versionar `EjecutorDeCapability` para transportar
  `data` — sigue abierta si la Opción A resulta insuficiente en la práctica.

### Actualización (revisión externa, Fase 17) — dos hallazgos de esta sección ya no son exactos

1. **`MemoryTool` ya no está rota.** `eon/tools/memory_tool.py` fue
   rediseñada: es autocontenida, sin ninguna dependencia de `eon.core`
   (que ya no existe en el árbol), configurable vía `EON_MEMORY_PATH`. Se
   verificó de forma independiente, ejecutándola en runtime
   (`agregar_conocimiento`/`buscar_conocimiento`, con la validación de
   `origen`/`evidencia` del Hallazgo 1 de la auditoría intacta) — funciona.
   `ToolRegistry.autodiscover()` ya no necesita saltarla.
2. **El Verifier real ya está conectado a `KernelRuntime`.** `runtime.py`
   construye por defecto `VerifierPortAdapter(Verifier())` — el Verifier
   de dominio real —, no `SimpleVerifier` (que ya no existe en el
   archivo). Verificado leyendo `runtime.py` directamente. Esto también
   corrige `KERNEL_CONSOLIDATION_REPORT.md` §5, que seguía sin
   actualizarse en esta rama del proyecto.

Ninguna de las dos correcciones anteriores modificó código de producción;
son puramente documentales, igual que esta misma nota.
