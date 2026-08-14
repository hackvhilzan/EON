"""
eon.planner.plan_selector
============================
Decide CUÁL de varios Planes candidatos (PLANNER.md §5) merece activarse.
Puramente funcional -- no toca PlannerStore ni emite eventos: solo recibe
Planes ya creados (estado `creado`) y devuelve cuál elegir. Escribir esa
decisión (activar el elegido, obsoletar el resto) es responsabilidad de
`PlannerManager`, igual que `plan_selector` no sabe nada de EventBus ni de
Objective.

El criterio de selección es un parámetro (`criterio`), nunca una decisión fija
de este módulo -- PLANNER.md §5 dice que el Planner "puede generar varios
planes candidatos" pero no fija cómo se elige entre ellos; eso es diseño de
implementación de la estrategia concreta del Planner (heurística, LLM,
puntuación por costo/confianza...), no parte del contrato de dominio.
"""

from __future__ import annotations

from collections.abc import Callable

from .models import Plan
from .planner_exceptions import InvalidPlanError

# Recibe la lista de candidatos y devuelve el índice del elegido.
CriterioSeleccion = Callable[[list[Plan]], int]


def primero(candidatos: list[Plan]) -> int:
    """Criterio por defecto: el primer candidato generado. Determinista y
    trivial -- útil como base y para tests; cualquier estrategia real de
    selección se inyecta como `criterio` en `seleccionar()`."""
    return 0


def menor_numero_de_tasks(candidatos: list[Plan]) -> int:
    """Ejemplo de criterio alternativo: preferir el candidato más simple (menos
    Tasks). No es normativo -- PLANNER.md no define ninguna heurística de
    selección; se ofrece solo como segunda opción lista para usar."""
    return min(range(len(candidatos)), key=lambda i: len(candidatos[i].tasks))


def seleccionar(candidatos: list[Plan], criterio: CriterioSeleccion = primero) -> Plan:
    """Aplica `criterio` sobre `candidatos` y devuelve el Plan elegido. No
    modifica ningún Plan -- solo indica cuál. Valida que todos los candidatos
    pertenezcan al mismo Objetivo (PLANNER.md Invariante 2: no tendría sentido
    elegir "el activo" entre Planes de distintos Objetivos) y que todos estén
    en `creado` (un candidato ya activo, obsoleto o cancelado no es un
    candidato pendiente de elegir)."""
    if not candidatos:
        raise InvalidPlanError("seleccionar() necesita al menos un Plan candidato.")

    objective_ids = {p.objective_id for p in candidatos}
    if len(objective_ids) > 1:
        raise InvalidPlanError("Todos los candidatos deben pertenecer al mismo Objetivo (PLANNER.md Invariante 1).")

    for candidato in candidatos:
        if candidato.estado.value != "creado":
            raise InvalidPlanError(
                f"El Plan '{candidato.id}' no es un candidato pendiente de elegir (estado: '{candidato.estado.value}')."
            )

    indice = criterio(candidatos)
    return candidatos[indice]
