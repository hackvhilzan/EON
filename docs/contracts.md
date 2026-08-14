# Contratos del Kernel EON

Este documento consolida los contratos de cada módulo de dominio. Los contratos originales (OBJECTIVES.md, PLANNER.md, etc.) viven en el código como docstrings; este es un resumen navegable.

## 1. Objectives (`eon/objectives/`)

### Estados (10)

```
pendiente → planificando → bloqueado → planificando (desbloqueo)
planificando → en_progreso
en_progreso → pausado → en_progreso (reanudar)
en_progreso → verificando
verificando → completado (éxito)
verificando → fallando → en_progreso (reintento) | fallido (agotado)
cancelable desde: pendiente, planificando, bloqueado, en_progreso, pausado
```

### Invariantes
- Estados terminales (completado, fallido, cancelado) nunca revierten.
- `verificar` es la única operación que mueve a verificando.
- `reintentar` requiere estado fallando. Lanza `RetryLimitExceededError` si agotado.

### API pública (ObjectivesPort en `coordinator/ports.py`)
- `crear_objetivo(descripcion, criterio_de_exito, **kwargs)`
- `planificar(objective_id, motivo)`
- `iniciar(objective_id)`
- `obtener(objective_id)`
- `verificar(objective_id, resultado)`
- `reintentar(objective_id)`
- `cancelar(objective_id, motivo)`

## 2. Planner (`eon/planner/`)

### Estados (4)
```
creado → activo → obsoleto
creado/activo/obsoleto → cancelado
```

### Invariantes
- Un Plan es inmutable salvo `estado`, `historial`, `actualizado_en`.
- Nunca dos Planes activos para el mismo Objetivo.
- Replanificar crea una versión nueva, nunca edita la anterior.
- Un Plan sin Tasks no es válido.

### API pública (PlannerPort)
- `planificar(objective_id, tasks: TaskSpec[])` → Plan
- `replanificar(objective_id, motivo, tasks: TaskSpec[])` → Plan

### Task (`eon/planner/task.py`)
- `capability_id` (requerido), `id`, `depende_de: tuple[str,...]`, `parametros: dict`
- Inmutable (`@dataclass(frozen=True)`)
- No puede depender de sí misma

## 3. Scheduler (`eon/scheduler/`)

### Estados
- Scheduler: `IDLE → RUNNING → STOPPED | CANCELLED`
- TaskExecution: `PENDING → READY → RUNNING → COMPLETED | FAILED | CANCELLED`

### Comportamiento
- Cálculo continuo de READY: tras cada `marcar_completed()`, recalcula dependencias.
- `marcar_running/completed/failed` exigen Scheduler en RUNNING.
- `cancelar()` detiene y cancela en cascada toda Task no terminal.

### API pública (SchedulerPort)
- `crear(plan_id)` → scheduler_id (coincide con plan_id)
- `iniciar(scheduler_id)`
- `cancelar(scheduler_id, motivo)`

## 4. Workers (`eon/workers/`)

### EjecutorDeCapability
```python
Callable[[str, dict], bool]  # (capability_id, parametros) -> ok
```
- El Kernel solo ve `bool`. El `data` del ToolResult no viaja por el Kernel.
- Nunca lanza excepciones hacia el llamador.

### Componentes
- `WorkerManager`: registro de workers por capability.
- `Dispatcher`: asigna Tasks a workers compatibles.
- `TaskExecutor`: ejecuta vía callable inyectado, emite `task_completada`/`task_fallida`.

## 5. Workspace (`eon/workspace/`)

### Estados (8)
```
CREATED → PLANNING → SCHEDULING → RUNNING → VERIFYING → COMPLETED
VERIFYING → PLANNING (replanificación)
Cualquier no-terminal → CANCELLED / FAILED
```

### Invariantes
- Un Objetivo raíz tiene como máximo un Workspace no terminal.
- El Coordinator es el único invocador autorizado de las transiciones.
- Artefactos con ownership por worker.

## 6. Package (`eon/package/`)

### Estados (5)
```
PENDING → BUILDING → READY | FAILED
Cualquier no-terminal → CANCELLED
```

### API pública (PackagePort)
- `solicitar(workspace_ref)` → Package (PENDING)
- `construir(package_id, workspace_ref)` → Package (READY | FAILED)

## 7. Gobernanza (`eon/governance/`)

### PolicyEngine
- Default-deny: si ninguna política permite, se deniega.
- Primera política que matcha gana.
- Decisiones: `ALLOW`, `DENY`, `REQUIRES_APPROVAL`.

### SandboxProfile
- `workspace_only()`: solo lee/escribe dentro del Workspace, sin red, 30s.
- `read_only()`: solo lectura, sin red, 15s.
- `network_restricted(allowlist)`: Workspace + red restringida, 60s.

### AuditLog
- Entradas inmutables con cadena de hashes (cada entrada incluye el hash de la anterior).
- Verificable criptográficamente.

## 8. Capabilities (`eon/capabilities/`)

### CapabilityExecutor
- Puente síncrono → asíncrono (event loop dedicado en hilo propio).
- `__call__(capability_id, parametros) -> bool`: nunca lanza.
- `ultimo_resultado`: `ToolResult` de la última invocación (solo lectura).
- `generar(prompt)`: acceso directo al LLM configurado.

### CapabilityMap
- Convención: `capability_id = "tool.<nombre>"`.
- `tool.terminal` y `tool.python` NO están en el mapa por defecto (riesgo alto).

## 9. LLM (`eon/llm/`)

### Providers (7)
- Claude (Anthropic), OpenAI (GPT), Gemini (Google), Ollama (local), Groq, OpenRouter, Fallback (cadena con failover).

### Interfaz
```python
class LLM(ABC):
    def generate(self, prompt: str) -> str: ...
```

### Configuración
- `EON_LLM_PROVIDER` env var.
- `get_provider_lazy()`: no construye hasta el primer `generate()`.

## 10. Task Generation (`eon/task_generation/`)

### TaskSpec
```python
@dataclass(frozen=True)
class TaskSpec:
    capability_id: str   # requerido
    id: str              # autogenerado
    depende_de: tuple    # ids de tasks precedentes
    parametros: dict
```

### Generadores
- `DeterministicTaskGenerator`: una Task `default` (para testing).
- `LLMTaskGenerator`: usa LLM para proponer TaskSpecs.
- `ValidatingTaskGenerator`: compone generador + `TaskSpecValidator`.

### Regla
> "El LLM propone. EON valida, gobierna y ejecuta."
