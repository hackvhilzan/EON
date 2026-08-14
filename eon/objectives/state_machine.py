"""
eon.objectives.state_machine
===============================
Valida TODAS las transiciones de estado de un Objetivo (OBJECTIVES.md §5). Regla
1 de la máquina: "Solo existen las transiciones dibujadas [en el contrato].
Cualquier otra combinación es un error de programación, no un caso a manejar con
un `if` más." Este módulo es la única fuente de verdad sobre qué transición es
legal y qué evento le corresponde -- ObjectiveManager nunca decide eso por su
cuenta, siempre pregunta aquí primero.

Este módulo NO toca ningún Objective ni el ObjectiveStore: es puramente
funciones de validación sin efectos secundarios. Escribir el Objetivo (historial,
`actualizado_en`, persistencia) y emitir el evento es responsabilidad de
ObjectiveManager, una vez que aquí se confirmó que la transición es legal.
"""

from __future__ import annotations

from . import events
from .exceptions import IllegalTransitionError
from .models import ObjectiveState as S

# (estado_origen, estado_destino) -> nombre_evento. Cada arista dibujada en el
# diagrama de §5 aparece aquí exactamente una vez.
TRANSICIONES: dict[tuple[S, S], str] = {
    (S.PENDIENTE, S.PLANIFICANDO): events.OBJETIVO_PLANIFICANDO,
    (S.PLANIFICANDO, S.BLOQUEADO): events.OBJETIVO_BLOQUEADO,
    (S.BLOQUEADO, S.PLANIFICANDO): events.OBJETIVO_DESBLOQUEADO,
    (S.PLANIFICANDO, S.EN_PROGRESO): events.OBJETIVO_INICIADO,
    (S.EN_PROGRESO, S.PAUSADO): events.OBJETIVO_PAUSADO,
    (S.PAUSADO, S.EN_PROGRESO): events.OBJETIVO_REANUDADO,
    (
        S.PAUSADO,
        S.BLOQUEADO,
    ): events.OBJETIVO_REANUDADO,  # §7: reanudar() cae a bloqueado si ya no se cumple una dependencia
    (S.EN_PROGRESO, S.VERIFICANDO): events.OBJETIVO_VERIFICANDO,
    (S.VERIFICANDO, S.COMPLETADO): events.OBJETIVO_COMPLETADO,
    (S.VERIFICANDO, S.FALLANDO): events.OBJETIVO_FALLANDO,  # ver nota en eon.objectives.events
    (S.FALLANDO, S.EN_PROGRESO): events.OBJETIVO_REINTENTANDO,
    (S.FALLANDO, S.FALLIDO): events.OBJETIVO_FALLIDO,
}

# cancelar() es un caso especial de la máquina: el mismo evento
# (objetivo_cancelado) es legal desde varios estados de origen distintos hacia el
# mismo destino terminal. Modelarlo como 5 entradas repetidas en TRANSICIONES
# ocultaría esa simetría; se deja aparte, tal como lo separa el propio diagrama
# de §5 en su última línea.
ESTADOS_CANCELABLES: frozenset[S] = frozenset({S.PENDIENTE, S.PLANIFICANDO, S.BLOQUEADO, S.EN_PROGRESO, S.PAUSADO})


def validar_transicion(origen: S, destino: S) -> str:
    """Devuelve el nombre del evento a emitir si `origen -> destino` es una
    transición legal. Lanza IllegalTransitionError si no lo es -- incluyendo el
    caso de que `origen` ya sea terminal (Invariante 2: no revierten)."""
    if origen.es_terminal:
        raise IllegalTransitionError(
            f"'{origen.value}' es un estado terminal: no puede transicionar a '{destino.value}'."
        )
    evento = TRANSICIONES.get((origen, destino))
    if evento is None:
        raise IllegalTransitionError(f"Transición no permitida: '{origen.value}' -> '{destino.value}'.")
    return evento


def validar_cancelacion(origen: S) -> str:
    """§8: cancelar() puede aplicarse desde pendiente, planificando, bloqueado,
    en_progreso o pausado -- no desde verificando/fallando (transitorios de la
    verificación) ni desde un estado ya terminal."""
    if origen not in ESTADOS_CANCELABLES:
        raise IllegalTransitionError(f"No se puede cancelar un Objetivo en estado '{origen.value}'.")
    return events.OBJETIVO_CANCELADO


def es_transicion_legal(origen: S, destino: S) -> bool:
    """Versión sin excepción de validar_transicion(), para código que solo
    necesita preguntar sin actuar (p.ej. construir una UI de qué botones mostrar)."""
    return not origen.es_terminal and (origen, destino) in TRANSICIONES
