"""
eon.workspace.validators
===========================
Valida las transiciones de `WorkspaceState` (WORKSPACE.md §3). Funciones
puras, sin efectos secundarios -- escribir el Workspace, anotar el
`historial` y emitir el evento es responsabilidad de `WorkspaceManager`.
"""

from __future__ import annotations

from . import events
from .exceptions import IllegalWorkspaceTransitionError
from .models import NO_TERMINALES
from .models import WorkspaceState as S

# (origen, destino) -> evento. WORKSPACE.md §3, tabla de transiciones
# legales. CANCELLED no aparece aquí como destino: cualquier estado no
# terminal puede cancelarse (regla aparte, ver `validar_transicion`).
TRANSICIONES: dict[tuple[S, S], str] = {
    (S.CREATED, S.PLANNING): events.WORKSPACE_PLANIFICANDO,
    (S.PLANNING, S.SCHEDULING): events.WORKSPACE_SCHEDULING,
    (S.SCHEDULING, S.RUNNING): events.WORKSPACE_EJECUTANDO,
    (S.RUNNING, S.VERIFYING): events.WORKSPACE_VERIFICANDO,
    (S.VERIFYING, S.COMPLETED): events.WORKSPACE_COMPLETADO,
    # Ciclo de replanificación (§3, "Regla de ciclo"): el Workspace no
    # distingue entre la primera vez en PLANNING y una vuelta tras rechazo
    # del Verifier -- ambas emiten el mismo evento.
    (S.VERIFYING, S.PLANNING): events.WORKSPACE_PLANIFICANDO,
    (S.VERIFYING, S.FAILED): events.WORKSPACE_FALLIDO,
    (S.RUNNING, S.FAILED): events.WORKSPACE_FALLIDO,
}


def validar_transicion(origen: S, destino: S) -> str:
    """Devuelve el evento que corresponde a `origen -> destino`, o lanza
    `IllegalWorkspaceTransitionError` si la transición no está permitida
    por WORKSPACE.md §3.

    `CANCELLED` es alcanzable desde cualquier estado no terminal (§3,
    "cualquier no-terminal -> CANCELLED") -- se resuelve aparte de la tabla
    fija porque su origen no es un único estado sino un conjunto.
    """
    if destino == S.CANCELLED:
        if origen in NO_TERMINALES:
            return events.WORKSPACE_CANCELADO
        raise IllegalWorkspaceTransitionError(
            f"Transición no permitida: '{origen.value}' -> '{destino.value}' "
            "(un estado terminal nunca revierte, WORKSPACE.md Invariante 5)."
        )
    if (origen, destino) not in TRANSICIONES:
        raise IllegalWorkspaceTransitionError(f"Transición no permitida: '{origen.value}' -> '{destino.value}'.")
    return TRANSICIONES[(origen, destino)]
