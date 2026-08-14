# Arquitectura del Kernel EON

## Visión general

EON es un kernel de control jerárquico para agentes autónomos. Un componente central (Coordinator) orquesta el flujo de ejecución, mientras que los módulos de dominio resuelven responsabilidades específicas: planificación, scheduling, ejecución, validación y entrega de resultados.

## Diagrama de capas

```
┌──────────────────────────────────────────────────┐
│                   CONSOLA HTTP                     │
│            (eon.console — REST API)                 │
└───────────────────────┬──────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────┐
│                  COORDINATOR                      │
│   Única autoridad de control del kernel            │
│   Máquina de 12 estados con recuperación          │
└──┬──────┬──────┬──────┬──────┬──────┬──────┬────┘
   │      │      │      │      │      │      │
┌──▼──┐┌──▼──┐┌──▼──┐┌──▼──┐┌──▼──┐┌──▼──┐┌──▼───┐
│ OBJ ││PLAN ││SCHED││WORK ││VERIF││WS   ││PKG   │
│     ││     ││     ││     ││     ││     ││      │
│10st ││ 4st ││ DAG ││Disp ││     ││ 8st ││ 5st  │
└─────┘└─────┘└─────┘└──┬──┘└─────┘└─────┘└──────┘
                        │
              ┌─────────┼─────────┐
              │         │         │
         ┌────▼──┐ ┌───▼───┐ ┌──▼────┐
         │TOOLS  │ │  LLM  │ │CAPAB  │
         │fs,llm │ │7 prov │ │sync→  │
         │       │ │       │ │async  │
         └───────┘ └───────┘ └───────┘
```

## Flujo de ejecución

```
1. Coordinator crea el Objetivo (Objectives)
2. Coordinator inicializa el Workspace
3. TaskGeneration genera TaskSpec[] desde el Objetivo
4. Coordinator solicita el Plan al Planner (con los TaskSpec[])
5. Coordinator lanza el Scheduler (resuelve dependencias DAG)
6. Workers ejecutan las Tasks (vía Dispatcher → CapabilityExecutor → Tools)
7. Verifier evalúa si el resultado cumple el criterio de éxito
8. Workspace conserva artefactos y estado
9. Package construye y entrega el resultado final empaquetado
```

Si el Verifier rechaza, el Coordinator puede replanificar (vuelta a paso 3) hasta agotar reintentos.

## Módulos de dominio

### Coordinator (`eon/coordinator/`)
- **Rol**: Único orquestador del kernel. No toma decisiones de dominio.
- **Estados**: CREATED → CREATING_OBJECTIVE → PLANNING → SCHEDULING → RUNNING → VERIFYING → (REPLANNING → PLANNING)* → AWAITING_WORKSPACE → PACKAGING → COMPLETED | FAILED | CANCELLED
- **Contrato**: `coordinator/ports.py` define Protocolos (Ports) que aíslan al Coordinator de cada módulo de dominio.

### Objectives (`eon/objectives/`)
- **Rol**: Ciclo de vida del objetivo y su estado.
- **Estados**: pendiente → planificando → (bloqueado)* → en_progreso → (pausado)* → verificando → (fallando → en_progreso)* → completado | fallido | cancelado
- **Reintentos**: Configurables (`max_reintentos`). `RetryLimitExceededError` cuando se agotan.

### Planner (`eon/planner/`)
- **Rol**: Crea y activa planes de trabajo. Versionado inmutable.
- **Estados**: creado → activo → obsoleto | cancelado
- **Multi-plan**: `generar_candidatos()` + `elegir_y_activar()` para estrategias alternativas.

### Scheduler (`eon/scheduler/`)
- **Rol**: Ordena Tasks y resuelve dependencias (DAG).
- **Cálculo continuo de READY**: Tras cada `marcar_completed()`, recalcula Tasks cuyas dependencias se satisficieron.
- **Cancelación en cascada**: `cancelar()` marca todas las Tasks no terminales como CANCELLED.

### Workers (`eon/workers/`)
- **Rol**: Ejecutan las Tasks asignadas.
- **Componentes**: `WorkerManager` (registro), `Dispatcher` (asignación), `TaskExecutor` (ejecución inyectable).
- **Invariante**: `EjecutorDeCapability = Callable[[str, dict], bool]` — el Kernel solo ve `ok/fail`, nunca datos.

### Workspace (`eon/workspace/`)
- **Rol**: Conserva contexto, artefactos y estado de ejecución.
- **Estados**: CREATED → PLANNING → SCHEDULING → RUNNING → VERIFYING → COMPLETED | FAILED | CANCELLED
- **Artefactos**: `ArtifactRegistry` con ownership por worker.

### Package (`eon/package/`)
- **Rol**: Construye el resultado final empaquetado.
- **Estados**: PENDING → BUILDING → READY | FAILED | CANCELLED

## Capacidades externas

### Tools (`eon/tools/`)
- **FilesystemTool**: read/write de archivos.
- **LLMTool**: expone un proveedor LLM como Tool invocable.
- **InternetTool**: búsqueda web con httpx (dep. opcional).
- **APITool**: llamadas HTTP arbitrarias con auth (dep. opcional: httpx).
- **DatabaseTool**: consultas SQL read-only (sqlite3 stdlib).
- **TerminalTool**: ejecución de comandos shell (bajo sandbox).
- **PythonTool**: ejecución de código Python (bajo sandbox).
- **PDFTool**: lectura/escritura de PDFs (dep. opcional: pypdf, reportlab).
- **ImageTool**: manipulación de imágenes (dep. opcional: Pillow).
- **EmailTool**: envío de emails via SMTP (smtplib stdlib).
- **CalendarTool**: Google Calendar API (dep. opcional: google-api-python-client).
- **BrowserTool**: navegación headless con Playwright (dep. opcional).
- **ToolRegistry**: registro por nombre, `autodiscover()` para registro automático, `discover()` para plugins.

### Plugins (`eon/plugins/`)
- **ToolPlugin**: empaqueta una Tool con metadatos de gobernanza (capability_id, policy, sandbox).
- **PluginLoader**: descubre plugins desde directorios y entry points.
  - Soporta `PLUGIN` (variable), `create_plugin()` y `create_tool()` (factory functions).
  - Hot-reload: `reload_directory()` recarga plugins sin reiniciar.
  - Config: `EON_PLUGINS_DIR` env var.

### SDK (`eon/sdk/`)
- Re-exporta `Tool`, `ToolResult`, `SandboxProfile`, `PolicyDecision`, `ToolPlugin`.
- Permite crear Tools custom sin instalar EON completo.
- Uso: `from eon.sdk import Tool, ToolResult`

### LLM (`eon/llm/`)
- **7 providers**: Claude, OpenAI, Gemini, Ollama, Groq, OpenRouter, Fallback.
- **Lazy loading**: `get_provider_lazy()` no construye el proveedor hasta el primer `generate()`.
- **Configuración**: `EON_LLM_PROVIDER` env var.

### Capabilities (`eon/capabilities/`)
- **CapabilityExecutor**: puente síncrono → asíncrono. Un event loop dedicado en un hilo.
- **CapabilityMap**: traduce `capability_id` (ej. `"tool.filesystem"`) a `Tool.execute(**kwargs)`.
- **Decisión**: el `data` del `ToolResult` no viaja por el Kernel (Opción A). Solo `ok`/`error` llega vía `bool`.

## Gobernanza (`eon/governance/`)

- **PolicyEngine**: default-deny. Evalúa cada Task antes del Dispatcher.
- **CapabilityPolicy**: declarativa, compuesta. Primera que matcha gana.
- **SandboxProfile**: perfiles de ejecución (workspace-only, read-only, network-restricted).
- **AuditLog**: entradas inmutables con cadena de hashes (verificable criptográficamente).

## EventBus (`eon/event_bus.py`)

- Síncrono: los callbacks se ejecutan en el mismo hilo que `emit`.
- Robusto: las excepciones de callbacks se capturan y no rompen el flujo.
- Compartido por todos los módulos del Kernel.

## Task Generation (`eon/task_generation/`)

- **DeterministicTaskGenerator**: genera una única Task `default` (para testing).
- **LLMTaskGenerator**: usa un LLM para proponer TaskSpecs desde el Objetivo.
- **ValidatingTaskGenerator**: compone generador + validador.
- **Regla**: "El LLM propone. EON valida, gobierna y ejecuta."

## Time Travel (`eon/timetravel/`)

- **TimeMachine**: reconstruye el estado del kernel en cualquier `event_seq` del EventStore.
- **Estrategia**: checkpoint-anchored reconstruction (read-only).
  1. Busca el último checkpoint con `event_seq <= target_seq`.
  2. Usa su `stores_state` como base exacta (verificada con hash SHA-256).
  3. Adjunta timeline de eventos entre `(base_seq, target_seq]`.
  4. Clasifica eventos en `applied` (reducer conocido) vs `unapplied` (preservados).
- **Honestidad**: si no hay checkpoint base, `complete=False` (reconstrucción parcial).
- **API**: `kernel.inspeccionar_en(execution_id, event_seq)`, `kernel.obtener_timeline(...)`.

## Execution Forking (`eon/forking/`)

- **ForkManager**: ramifica una ejecución desde un checkpoint.
- **Semántica**: deep-copy del estado del checkpoint en nuevos IDs. La ejecución original no se muta.
- **ExecutionFork**: registro durable con `parent_execution_id`, `parent_checkpoint_id`, `forked_at_seq`, `new_execution_id`.
- **SQLiteForkStore**: persistencia durable, sobrevive reinicios.
- **comparar_forks()**: diff read-only de estados base de dos forks.
- **obtener_fork_tree()**: jerarquía de forks anidados.
- **Evento**: `execution.forked` se emite al EventStore.
- **API**: `kernel.fork_from_checkpoint(checkpoint_id)`, `kernel.comparar_forks(...)`, `kernel.obtener_fork_tree(...)`.

## Verificación Avanzada (`eon/verification/`)

- **CompositeVerifier**: combina 5 capas de verificación y agrega confianza.
- **Capas**:
  1. `StructuralVerifier` — verifica que todas las Tasks se completaron y los artefactos existen.
  2. `CriteriaVerifier` — parsea criterios de éxito medibles (contains, size, structured).
  3. `LLMJudgeVerifier` — un LLM evalúa el resultado contra el criterio (protocolo inyectable).
  4. `TestBasedVerifier` — genera y ejecuta tests automáticos en subprocess.
  5. `ExternalVerifier` — hooks para validadores externos (linters, type checkers). Deshabilitado por defecto.
- **EvidenceGraph**: grafo que conecta Task → ToolResult → artefacto → criterio verificado.
- **ConfidenceCalibrator**: pesos configurables por capa, renormaliza sobre capas aplicables.
- **Design**: las capas no aplicables devuelven `SKIPPED` (no `FAIL`). El CompositeVerifier falla si cualquier capa aplicable devuelve `FAIL`.

## Memoria Avanzada (`eon/memory/`)

- **EpisodicMemoryStore**: persiste ejecuciones completas como episodios (SQLite). Búsqueda por similitud de descripción (Jaccard).
- **SemanticMemory**: vector store con `HashingEmbedder` determinista (sin dependencias externas). Búsqueda por similitud coseno.
- **SkillLibrary**: planes exitosos reutilizables y versionados. Match por similitud, tracking de `success_rate`.
- **FailurePatterns**: categorización de fallos (7 categorías del enum `FailureCategory`: TASK_FAILED, CAPABILITY_UNAVAILABLE, TIMEOUT, VERIFIER_REJECTION, POLICY_DENIED, LLM_ERROR, TOOL_ERROR, UNKNOWN). Estadísticas por capability y categoría.
- **ExecutionReplayer**: reproduce ejecuciones paso a paso desde el EventStore, con descripciones legibles.
- **VerificationWeightLearner**: ajuste online de pesos de verificación con decay exponencial y prior bayesiano.

## Planificación Inteligente (`eon/planning/`)

- **PlanScorer**: evalúa planes con 4 métricas deterministas (efficiency, robustness, coverage, simplicity). `rank()` ordena múltiples planes.
- **PlanSimulator**: dry-run que predice éxito, confianza, coste y duración. Detecta bottlenecks y capabilities faltantes. Consulta histórico opcional.
- **ObjectiveDecomposer**: descompone objetivos complejos en sub-objetivos por delimitadores heurísticos (y, además, luego, ;). `max_depth` anti-loop.
- **AutoReplanner**: replanificación contextual (no simple retry). Clasifica 8 tipos de fallo (timeout, assertion_error, rate_limit, not_found, permission_denied, syntax_error, network_error, unknown) y selecciona estrategia (timeout → aumentar timeout, rate_limit → backoff, not_found → capability alternativa, assertion_error → enfoque alternativo, etc.). Busca skills alternativas en SkillLibrary.

## Principios de diseño

1. **La autoridad de control reside en el Coordinator.**
2. **Planner, Scheduler, Workers, Workspace y Package responden a esa orquestación.**
3. **Tools y LLM son capacidades de apoyo, no componentes de gobierno.**
4. **Las dependencias entre módulos respetan contratos (Ports/Protocols) y no rompen la jerarquía.**
5. **Default-deny**: sin política explícita que permita, se deniega.
6. **Sin ejecuciones silenciosas**: toda decisión se audita.

## Fase 10: Integración final (`eon/intelligence.py`, `eon/telemetry/`, `eon/console/`)

Los módulos de las Fases 7-9 se integran con el runtime mediante una capa no-invasiva de hooks opt-in.

### Capa de inteligencia (`eon/intelligence.py`)

- **IntelligenceConfig**: configuración opt-in para cada feature (plan scoring, simulation, auto-replanning, episodic memory, semantic memory, skill library, failure patterns, verification learning, composite verifier, console).
- **IntelligenceHooks**: hooks llamados por `EnhancedKernelRuntime` en puntos clave del lifecycle:
  - `on_plan_created()`: scoring + simulación
  - `on_verification_rejected()`: AutoReplanner sugiere replanificación
  - `on_execution_completed()`: persiste episodio, registra skill, registra fallo, actualiza pesos
  - `on_verification_result()`: métricas de confianza y dictamen
- **EnhancedKernelRuntime**: subclass de `KernelRuntime` con inteligencia wired in. No modifica el Coordinator ni su state machine. Si no se configura, comportamiento idéntico al base.

### Adaptadores

- **CompositeVerifierAdapter** (`eon/verification/runtime_adapter.py`): puentea el CompositeVerifier (Fase 8) con el VerifierPort del Coordinator. Preserva el contrato booleano (`dictamen`, `confianza`, `justificacion`). Almacena `last_result` para métricas/memoria/replan.
- **LLMJudgeAdapter** (`eon/llm/adapters.py`): adapta un `LLM` provider al `JudgeProtocol` del `LLMJudgeVerifier`. Parsea JSON con fallback regex.
- **DecomposerLLMAdapter** (`eon/llm/adapters.py`): adapta un `LLM` provider para `ObjectiveDecomposer`.

### ChromaDB opcional (`eon/memory/chroma_backend.py`)

- `SemanticMemory.create_backend(backend="memory"|"chroma")`: factory method.
- Import lazy de `chromadb`. Fallback automático a `InMemoryVectorStore` si no está instalado.
- **Persistencia real**: `PersistentClient(path=...)` escribe al disco en cada operación. Los datos sobreviven a reinicios.
- **Embeddings nativos de ChromaDB**: modelo all-MiniLM-L6-v2 (ONNX) por defecto, o `EmbeddingProvider` personalizado.
- **Distancia configurable**: `"cosine"` (por defecto), `"l2"`, `"ip"`.
- **Métodos**: `add()`, `search()`, `delete(ids, where)`, `clear()`, `destroy()`, `close()`.
- **Sanitización de metadatos**: tipos no primitivos → string, None → omitido, placeholder interno para dicts vacíos.

### Telemetría (`eon/telemetry/`)

- `MetricsRecorder`: counters, gauges, histograms (p50/p95/p99). Thread-safe. Zero dependencias.
- `snapshot()`: dict JSON-serializable para exposición vía API.

### Consola REST (`eon/console/`)

- `ConsoleServer`: HTTP/HTTPS server con `http.server` (stdlib). Endpoints: `/health`, `/executions`, `/executions/{id}`, `/metrics`, `/run` (POST).
- `ThreadingHTTPServer` en hilo daemon — non-blocking.
- **AuthProvider**: autenticación Bearer token con comparación timing-safe (`hmac.compare_digest`). `/health` siempre público.
- **TLSCertGenerator**: auto-genera certificados self-signed (openssl o cryptography). `tls_auto_generate=True`.
- **TLS**: `ssl.SSLContext` wrap del socket. Propiedad `url` usa `https://` cuando TLS activo.