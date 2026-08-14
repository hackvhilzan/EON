"""
eon.planner.planner_events
=============================
Nombres de los eventos del dominio de Plan (PLANNER.md §9), publicados por
PlannerManager en el mismo EventBus que ya usa eon.objectives -- no se crea un
segundo bus paralelo.

Cada nombre sigue el mismo patrón `plan_<estado o transición>` que
`eon.objectives.events` usa para `objetivo_<...>`. Los cinco eventos son
exactamente los que nombra PLANNER.md §9, sin añadidos: a diferencia de
OBJECTIVES.md §6 (que dejaba un hueco real para `verificando -> fallando`,
resuelto en `eon.objectives.events` con `OBJETIVO_FALLANDO`), la máquina de
PLANNER.md §8 -- tras el cierre del contrato -- ya cubre todas sus
transiciones con los cinco eventos declarados:

- `plan_creado`       : nace un Plan por planificación inicial o reintento (§2, situaciones 1 y 3).
- `plan_replanificado`: nace un Plan por replanificación (§2, situación 2). Nunca junto a `plan_creado` (§9).
- `plan_activado`     : `creado -> activo`.
- `plan_obsoleto`     : `activo -> obsoleto` (al activarse otro Plan del mismo Objetivo), o
                         `creado -> obsoleto` (un candidato no elegido, §5/§8).
- `plan_cancelado`    : `creado|activo|obsoleto -> cancelado` (§8, incluida la cascada desde
                         la cancelación del Objetivo asociado).
"""

from __future__ import annotations

PLAN_CREADO = "plan_creado"
PLAN_REPLANIFICADO = "plan_replanificado"
PLAN_ACTIVADO = "plan_activado"
PLAN_OBSOLETO = "plan_obsoleto"
PLAN_CANCELADO = "plan_cancelado"

TODOS = frozenset(
    {
        PLAN_CREADO,
        PLAN_REPLANIFICADO,
        PLAN_ACTIVADO,
        PLAN_OBSOLETO,
        PLAN_CANCELADO,
    }
)

# Eventos que representan el "nacimiento" de un Plan (PLANNER.md §2). Nunca se
# emiten ambos para el mismo Plan -- son mutuamente excluyentes (§9).
EVENTOS_DE_NACIMIENTO = frozenset({PLAN_CREADO, PLAN_REPLANIFICADO})
