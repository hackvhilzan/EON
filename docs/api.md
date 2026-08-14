# API REST — Consola de EON

Base URL: `http://127.0.0.1:8765`

## Health Check

### `GET /estado`

Devuelve el estado del servidor.

**Respuesta 200:**
```json
{
  "status": "ok"
}
```

## Ejecuciones

### `GET /ejecuciones`

Lista todas las ejecuciones del Coordinator.

**Respuesta 200:**
```json
{
  "ejecuciones": [
    {
      "id": "uuid",
      "estado": "completed",
      "objective_id": "uuid",
      "plan_id": "uuid",
      "package_id": "uuid"
    }
  ]
}
```

Estados posibles del Coordinator: `created`, `creating_objective`, `planning`, `scheduling`, `running`, `verifying`, `replanning`, `awaiting_workspace`, `packaging`, `completed`, `failed`, `cancelled`.

### `GET /ejecuciones/{execution_id}`

Devuelve el detalle de una ejecución específica.

**Respuesta 200:**
```json
{
  "id": "uuid",
  "estado": "completed",
  "objective_id": "uuid",
  "plan_id": "uuid",
  "workspace_id": "uuid",
  "package_id": "uuid"
}
```

**Respuesta 404:**
```json
{
  "error": "Ejecución no encontrada: {execution_id}"
}
```

### `POST /ejecuciones`

Crea y ejecuta una nueva objetivo de forma síncrona.

**Request body:**
```json
{
  "descripcion": "string (requerido)",
  "criterio_de_exito": "string (requerido)",
  "configuracion": {}
}
```

**Respuesta 201:**
```json
{
  "execution_id": "uuid",
  "package_id": "uuid",
  "package_state": "ready"
}
```

**Respuesta 400:**
```json
{
  "error": "Se requieren 'descripcion' y 'criterio_de_exito'."
}
```

**Respuesta 500:**
```json
{
  "error": "Descripción del error"
}
```

## Ejemplos

### curl

```bash
# Health check
curl http://127.0.0.1:8765/estado

# Listar ejecuciones
curl http://127.0.0.1:8765/ejecuciones

# Crear ejecución
curl -X POST http://127.0.0.1:8765/ejecuciones \
  -H "Content-Type: application/json" \
  -d '{"descripcion": "Hola", "criterio_de_exito": "Termina OK"}'

# Ver detalle
curl http://127.0.0.1:8765/ejecuciones/{id}
```

### Python (httpx)

```python
import httpx

resp = httpx.post(
    "http://127.0.0.1:8765/ejecuciones",
    json={
        "descripcion": "Procesar datos",
        "criterio_de_exito": "Sin errores",
    },
)
print(resp.json())
```

## Notas

- La consola usa `http.server` de la stdlib (sin dependencias externas).
- El servidor es single-user/local por diseño (ver `CONSOLE.md`).
- POST `/ejecuciones` es síncrono: bloquea hasta que la ejecución termina.
- Docker expone el puerto 8765 con health check en `/estado`.
