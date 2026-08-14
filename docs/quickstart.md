# EON — Guía de Inicio Rápido

## Requisitos

- Python 3.11+
- pip

## Instalación

```bash
# Clonar el repositorio
git clone <repo-url>
cd eon-kernel

# Instalar en modo desarrollo (incluye herramientas de testing/linting)
make dev-install

# O instalar solo dependencias runtime
make install
```

## Ejecutar tests

```bash
# Todos los tests
make test

# Con cobertura
make test-cov

# Solo tests rápidos (excluir slow/integration/e2e)
make test-quick
```

## Linting y type checking

```bash
# Lint
make lint

# Formatear
make format

# Type check
make typecheck

# Todo en uno (CI local)
make check
```

## Levantar la consola HTTP

```bash
# Consola en http://127.0.0.1:8765
make console

# O con opciones personalizadas
python3 -m eon.console --root /data --host 0.0.0.0 --port 8765
```

### Endpoints disponibles

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/estado` | Health check |
| GET | `/ejecuciones` | Listar ejecuciones |
| GET | `/ejecuciones/{id}` | Detalle de una ejecución |
| POST | `/ejecuciones` | Crear una nueva ejecución |

### Crear una ejecución

```bash
curl -X POST http://127.0.0.1:8765/ejecuciones \
  -H "Content-Type: application/json" \
  -d '{
    "descripcion": "Generar un saludo",
    "criterio_de_exito": "La ejecución termina sin error"
  }'
```

Respuesta:
```json
{
  "execution_id": "...",
  "package_id": "...",
  "package_state": "ready"
}
```

## Usar EON desde Python

```python
from eon.runtime import KernelRuntime

# Ejecución básica (sin LLM, sin Tools)
runtime = KernelRuntime()
result = runtime.run(
    descripcion="Procesar datos",
    criterio_de_exito="Todas las tasks completadas",
)
print(f"Execution: {result.execution_id}")
print(f"Package state: {result.package_state}")
```

### Con Tools y LLM

```python
from eon.runtime import KernelRuntime
from eon.capabilities import CapabilityExecutor

with CapabilityExecutor() as executor:
    runtime = KernelRuntime(task_executor=executor)
    result = runtime.run(
        descripcion="Leer un archivo y resumirlo",
        criterio_de_exito="Resumen generado",
    )
```

## Docker

```bash
# Construir
make docker-build

# Ejecutar
make docker-run
```

## Configuración

Copia `.env.example` a `.env` y ajusta:

```env
EON_LLM_PROVIDER=claude     # claude|openai|gemini|ollama|groq|openrouter|fallback
ANTHROPIC_API_KEY=sk-...     # solo si usas Claude
```

## Estructura del proyecto

```
eon-kernel/
├── eon/                    # Código del kernel
│   ├── coordinator/        # Orquestador (12 estados)
│   ├── objectives/         # Gestión de objetivos (10 estados)
│   ├── planner/            # Planificación (4 estados)
│   ├── scheduler/          # Resolución de DAGs
│   ├── workers/            # Ejecución de tasks
│   ├── workspace/          # Artefactos y contexto (8 estados)
│   ├── package/            # Empaquetado de resultados (5 estados)
│   ├── task_generation/    # Generación de TaskSpecs
│   ├── governance/         # PolicyEngine, AuditLog, Sandbox
│   ├── capabilities/       # Puente sync→async Tools/LLM
│   ├── tools/              # Filesystem, LLM tools
│   ├── llm/                # 7 providers
│   ├── console/            # Servidor HTTP REST
│   ├── runtime.py          # KernelRuntime (composition root)
│   └── event_bus.py        # EventBus síncrono
├── docs/                   # Documentación
├── scripts/                # Scripts (smoke test, etc.)
├── tests/                  # ( futuro: tests de integración )
├── pyproject.toml           # Config del proyecto
├── Makefile                # Comandos de desarrollo
├── Dockerfile              # Imagen de la consola
└── docker-compose.yml      # Orquestación local
```
