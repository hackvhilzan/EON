"""
eon.coordinator.validators
=============================
Único punto que decide si una transición de `CoordinatorState` es legal
(mismo patrón que `eon.package.validators`, no incluido en el paquete de la
Fase 12 pero replicado aquí a partir de `PackageManager` -- ver informe).
`CoordinatorManager` nunca compara estados a mano: siempre pasa por
`validar_transicion()`.
"""

from __future__ import annotations

from . import events
from .exceptions import IllegalCoordinatorTransitionError
from .models import NO_TERMINALES, CoordinatorState

# Transiciones legales explícitas del ORDEN DE EJECUCIÓN (ORDEN MAESTRA).
_TRANSICIONES: dict[tuple[CoordinatorState, CoordinatorState], str] = {
    (CoordinatorState.CREATED, CoordinatorState.CREATING_OBJECTIVE): events.EJECUCION_OBJETIVO_SOLICITADO,
    (CoordinatorState.CREATING_OBJECTIVE, CoordinatorState.PLANNING): events.EJECUCION_PLANIFICANDO,
    (CoordinatorState.PLANNING, CoordinatorState.SCHEDULING): events.EJECUCION_SCHEDULING,
    (CoordinatorState.SCHEDULING, CoordinatorState.RUNNING): events.EJECUCION_EJECUTANDO,
    (CoordinatorState.RUNNING, CoordinatorState.VERIFYING): events.EJECUCION_VERIFICANDO,
    (CoordinatorState.VERIFYING, CoordinatorState.REPLANNING): events.EJECUCION_REPLANIFICANDO,
    (CoordinatorState.REPLANNING, CoordinatorState.PLANNING): events.EJECUCION_PLANIFICANDO,
    (CoordinatorState.VERIFYING, CoordinatorState.AWAITING_WORKSPACE): events.EJECUCION_ESPERANDO_WORKSPACE,
    (CoordinatorState.AWAITING_WORKSPACE, CoordinatorState.PACKAGING): events.EJECUCION_EMPAQUETANDO,
    (CoordinatorState.PACKAGING, CoordinatorState.COMPLETED): events.EJECUCION_COMPLETADA,
}

# Cualquier estado no-terminal puede caer a FAILED o a CANCELLED en el mismo
# acto (mismo criterio que WORKSPACE.md §3: "cualquier no-terminal ->
# CANCELLED" y OBJECTIVES.md §8: cancelación desde cualquier no-terminal).
for _origen in NO_TERMINALES:
    _TRANSICIONES[(_origen, CoordinatorState.FAILED)] = events.EJECUCION_FALLIDA
    _TRANSICIONES[(_origen, CoordinatorState.CANCELLED)] = events.EJECUCION_CANCELADA


def validar_transicion(origen: CoordinatorState, destino: CoordinatorState) -> str:
    """Devuelve el evento correspondiente a `origen -> destino`, o lanza
    `IllegalCoordinatorTransitionError` si la transición no está permitida.
    Los estados terminales nunca aparecen como `origen` (ORDEN MAESTRA:
    los tres son terminales de verdad, igual que OBJECTIVES.md §5.2 y
    WORKSPACE.md Invariante 5)."""
    clave = (origen, destino)
    if clave not in _TRANSICIONES:
        raise IllegalCoordinatorTransitionError(f"Transición ilegal: {origen.value!r} -> {destino.value!r}.")
    return _TRANSICIONES[clave]
