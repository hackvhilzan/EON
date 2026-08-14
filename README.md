# -eon-kernel

## Arquitectura del kernel

El kernel de EON está organizado como una arquitectura de control jerárquico en la que un componente central orquesta el flujo de ejecución, mientras que los demás módulos resuelven responsabilidades específicas de dominio, ejecución, validación y entrega de resultados.

### Capas principales

- Coordinator: gobierna el flujo completo del kernel y actúa como punto único de orquestación.
- Objectives: gestiona el ciclo de vida del objetivo y su estado.
- Planner: crea y activa planes de trabajo.
- Scheduler: ordena tareas y resuelve dependencias.
- Workers: ejecutan las tareas asignadas.
- Verifier: evalúa si los resultados cumplen el criterio de éxito.
- Workspace: conserva contexto, artefactos y estado de ejecución.
- Package: construye el resultado final empaquetado.

### Capacidades externas

- Tools: aportan capacidades concretas de acción (filesystem, internet, API, database, terminal, python, PDF, image, email, calendar, browser).
- Plugins: sistema de plugins para registrar Tools custom sin tocar el core.
- SDK: paquete mínimo para crear Tools externas.
- LLM: aporta generación o razonamiento, pero no gobierna el kernel.

### Capacidades avanzadas

- Checkpointing: captura del estado completo de una ejecución con hash verificable.
- HITL: Human-in-the-Loop interrupt/resume con interrupciones persistentes.
- Tracing: observabilidad ligera con spans OTel-shaped.
- Time Travel: reconstrucción read-only del estado en cualquier event_seq.
- Execution Forking: ramificación de ejecuciones desde checkpoints.
- Verificación Avanzada: verificación multi-capa con EvidenceGraph y ConfidenceCalibrator.
- Memoria Avanzada: memoria episódica, semántica, skills, patrones de fallo, replay y aprendizaje de pesos.
- Planificación Inteligente: scoring de planes, simulación dry-run, descomposición de objetivos y auto-replanificación contextual.
- Integración opt-in: capa de inteligencia no-invasiva que conecta planificación, verificación y memoria con el runtime mediante hooks. No modifica el Coordinator.
- ChromaDB opcional: backend de vector store para memoria semántica con **persistencia real** (datos que sobreviven a reinicios), embeddings nativos all-MiniLM-L6-v2, distancia configurable y fallback automático al HashingEmbedder determinista.
- Adapters LLM: puentes entre el provider LLM del kernel y el LLMJudgeVerifier / ObjectiveDecomposer.
- Telemetría: counters, gauges y histograms (p50/p95/p99) sin dependencias externas.
- Consola REST: API HTTP/HTTPS con `http.server` — endpoints `/health`, `/executions`, `/metrics`, `/run`. Autenticación Bearer token (timing-safe) y TLS con auto-generación de certificados self-signed.

### Principios de diseño

- La autoridad de control reside en el Coordinator.
- Planner, Scheduler, Workers, Workspace y Package responden a esa orquestación.
- Tools y LLM son capacidades de apoyo, no componentes de gobierno.
- Las dependencias entre módulos deben respetar contratos y no romper la jerarquía de control del kernel.

### Flujo de ejecución

1. El Coordinator crea el objetivo.
2. El Coordinator inicializa el workspace.
3. El Coordinator solicita un plan al Planner.
4. El Coordinator lanza el Scheduler.
5. Los Workers ejecutan las tareas.
6. El Verifier evalúa el resultado.
7. El Workspace conserva el contexto y los artefactos.
8. El Package entrega el resultado final.
