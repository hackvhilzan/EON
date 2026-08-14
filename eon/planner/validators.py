"""
eon.planner.validators
=========================
Valida TODAS las transiciones de estado de un Plan (PLANNER.md §8) y los
invariantes de contenido que no dependen de tener ya un PlannerStore
(Invariantes 5-8, delegadas en `task_graph.TaskGraph`).

Regla de la máquina (PLANNER.md §8): solo existen las transiciones dibujadas
en el contrato -- incluidas las tres reglas de cierre añadidas al congelar:
`cancelado` alcanzable desde `creado`, `activo` y `obsoleto`; un candidato no
elegido pasa de `creado` a `obsoleto` directamente; y el Plan activo de un
Objetivo cancelado pasa a `cancelado`. Este módulo es la única fuente de
verdad sobre qué transición es legal y qué evento le corresponde --
PlannerManager nunca decide eso por su cuenta.

Este módulo NO toca ningún Plan ni el PlannerStore: son funciones de
validación puras, sin efectos secundarios. Escribir el Plan (historial,
`actualizado_en`, persistencia) y emitir el evento es responsabilidad de
PlannerManager, una vez que aquí se confirmó que la transición es legal.
"""

from __future__ import annotations

from . import planner_events as events
from .models import PlanState as S
from .planner_exceptions import IllegalPlanTransitionError, InvalidPlanError

# (estado_origen, estado_destino) -> nombre_evento. Cada arista de §8 aparece
# aquí exactamente una vez. `creado -> activo` y `activo -> obsoleto` son las
# dos únicas aristas "de ciclo de vida normal"; el resto de destinos
# (`obsoleto` por descarte de candidato, `cancelado` desde varios orígenes) se
# resuelven en sus propias funciones más abajo, igual que `objetivo_cancelado`
# se separa de TRANSICIONES en eon.objectives.state_machine.
TRANSICIONES: dict[tuple[S, S], str] = {
    (S.CREADO, S.ACTIVO): events.PLAN_ACTIVADO,
    (S.ACTIVO, S.OBSOLETO): events.PLAN_OBSOLETO,
}

# §8 (regla de cierre): un Plan candidato generado pero nunca activado pasa
# directamente de `creado` a `obsoleto` en el acto en que se activa otro
# candidato del mismo Objetivo. Comparte evento con la obsolescencia normal
# (`plan_obsoleto`) -- PLANNER.md §9 no distingue un tercer evento para este
# caso, solo aclara la transición adicional en §8.
TRANSICION_CANDIDATO_DESCARTADO: tuple[S, S] = (S.CREADO, S.OBSOLETO)

# §8 (regla de cierre): cancelado es alcanzable desde estos tres orígenes.
ESTADOS_CANCELABLES: frozenset[S] = frozenset({S.CREADO, S.ACTIVO, S.OBSOLETO})


def validar_transicion(origen: S, destino: S) -> str:
    """Devuelve el nombre del evento a emitir si `origen -> destino` es una
    transición legal del ciclo de vida normal (activación / obsolescencia por
    reemplazo). Lanza IllegalPlanTransitionError si no lo es -- incluyendo el
    caso de que `origen` ya sea terminal (un Plan cancelado nunca vuelve
    atrás, §8)."""
    if origen.es_terminal:
        raise IllegalPlanTransitionError(
            f"'{origen.value}' es un estado terminal: no puede transicionar a '{destino.value}'."
        )
    evento = TRANSICIONES.get((origen, destino))
    if evento is None:
        raise IllegalPlanTransitionError(f"Transición no permitida: '{origen.value}' -> '{destino.value}'.")
    return evento


def validar_descarte_candidato(origen: S) -> str:
    """§8 (regla de cierre): un candidato no elegido solo puede descartarse
    desde `creado` -- nunca se genera un candidato ya activo (activar() ya lo
    habría sacado de la ronda de selección) ni ya obsoleto."""
    if origen != S.CREADO:
        raise IllegalPlanTransitionError(
            f"Solo se puede descartar como candidato un Plan en 'creado' (actual: '{origen.value}')."
        )
    return events.PLAN_OBSOLETO


def validar_cancelacion(origen: S) -> str:
    """§8 (regla de cierre): cancelar() puede aplicarse desde creado, activo u
    obsoleto -- no desde un estado ya terminal."""
    if origen not in ESTADOS_CANCELABLES:
        raise IllegalPlanTransitionError(f"No se puede cancelar un Plan en estado '{origen.value}'.")
    return events.PLAN_CANCELADO


def es_transicion_legal(origen: S, destino: S) -> bool:
    """Versión sin excepción de validar_transicion(), para código que solo
    necesita preguntar sin actuar."""
    return not origen.es_terminal and (origen, destino) in TRANSICIONES


def validar_tasks_no_vacias(tasks: list) -> None:
    """§1: un Plan sin Tasks no es una estrategia válida. Ya lo exige
    `Plan.__post_init__`; se expone también aquí para quien quiera validar una
    lista de Tasks ANTES de construir el Plan (p.ej. strategy_builder, al
    ensamblar un candidato)."""
    if not tasks:
        raise InvalidPlanError("Un Plan sin Tasks no es una estrategia válida (PLANNER.md §1).")
