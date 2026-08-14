"""Catálogo de eventos publicados por Coordinator (Fase 13).

El Coordinator tiene su propia entidad de dominio -- la `CoordinatorExecution`
-- distinta de Objetivo, Plan, Task, Verificación, Workspace o Package.
Cada transición de su máquina de estados (ver `models.py`) emite exactamente
uno de estos eventos, con el mismo criterio de "ninguna transición
silenciosa" que el resto del Kernel (OBJECTIVES.md §6, PLANNER.md §9,
SCHEDULER.md §10, VERIFIER.md §10, WORKSPACE.md §7, PACKAGE.md §7).

Prefijo `ejecucion_` para no colisionar con los eventos de dominio de los
módulos delegados (`objetivo_*`, `plan_*`, `task_*`, `scheduler_*`,
`verificacion_*`, `workspace_*`, `package_*`), que el Coordinator consume
pero nunca emite en su propio nombre.
"""

from __future__ import annotations

EJECUCION_CREADA = "ejecucion_creada"
EJECUCION_OBJETIVO_SOLICITADO = "ejecucion_objetivo_solicitado"
EJECUCION_PLANIFICANDO = "ejecucion_planificando"
EJECUCION_SCHEDULING = "ejecucion_scheduling"
EJECUCION_EJECUTANDO = "ejecucion_ejecutando"
EJECUCION_VERIFICANDO = "ejecucion_verificando"
EJECUCION_REPLANIFICANDO = "ejecucion_replanificando"
EJECUCION_ESPERANDO_WORKSPACE = "ejecucion_esperando_workspace"
EJECUCION_EMPAQUETANDO = "ejecucion_empaquetando"
EJECUCION_COMPLETADA = "ejecucion_completada"
EJECUCION_FALLIDA = "ejecucion_fallida"
EJECUCION_CANCELADA = "ejecucion_cancelada"
EJECUCION_RECUPERADA = "ejecucion_recuperada"

TODOS = frozenset(
    {
        EJECUCION_CREADA,
        EJECUCION_OBJETIVO_SOLICITADO,
        EJECUCION_PLANIFICANDO,
        EJECUCION_SCHEDULING,
        EJECUCION_EJECUTANDO,
        EJECUCION_VERIFICANDO,
        EJECUCION_REPLANIFICANDO,
        EJECUCION_ESPERANDO_WORKSPACE,
        EJECUCION_EMPAQUETANDO,
        EJECUCION_COMPLETADA,
        EJECUCION_FALLIDA,
        EJECUCION_CANCELADA,
        EJECUCION_RECUPERADA,
    }
)

# Eventos externos que el Coordinator CONSUME (no emite) para reaccionar,
# publicados por los módulos que sí coordina (ORDEN MAESTRA, "EVENTOS":
# "Siempre reaccionará a eventos publicados"). Documentados aquí como
# referencia única de qué escucha `manager.py`, no como catálogo propio.
PLAN_ACTIVADO = "plan_activado"  # PLANNER.md §9
SCHEDULER_FINALIZADO = "scheduler_finalizado"  # SCHEDULER.md §10
VERIFICACION_APROBADA = "verificacion_aprobada"  # VERIFIER.md §10
VERIFICACION_RECHAZADA = "verificacion_rechazada"  # VERIFIER.md §10
VERIFICACION_REINTENTO = "verificacion_reintento"  # VERIFIER.md §10
WORKSPACE_COMPLETADO = "workspace_completado"  # WORKSPACE.md §7
WORKSPACE_FALLIDO = "workspace_fallido"  # WORKSPACE.md §7
PACKAGE_LISTO = "package_listo"  # PACKAGE.md §7
PACKAGE_FALLIDO = "package_fallido"  # PACKAGE.md §7
