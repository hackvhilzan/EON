"""
eon.objectives.events
========================
Nombres de los eventos del dominio de Objetivos (OBJECTIVES.md §6), publicados
por ObjectiveManager a través de `EventBus` (definido en este mismo módulo).

Nota de implementación (justificada, no una preferencia de diseño -- ver el
preámbulo del propio contrato: "cualquier modificación debe justificarse por una
limitación descubierta durante la implementación"): la tabla de §6 no nombra un
evento para la transición `verificando -> fallando` (solo nombra
`objetivo_verificando`, `objetivo_completado`, `objetivo_reintentando` y
`objetivo_fallido`), pero esa transición sí existe en el diagrama de §5
("confianza < confianza_minima") y la Invariante 3 exige que TODA transición
emita su evento correspondiente sin excepción. `OBJETIVO_FALLANDO` cierra ese
hueco seleccionado, siguiendo el mismo patrón `objetivo_<estado>` que el resto de
eventos de la máquina de estados. Es la única adición sobre la lista explícita
de §6.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable


class EventBus:
    """Bus de eventos síncrono, mínimo, local al dominio de Objetivos.

    Antes vivía en el `core` de la Fase Ω (eliminado del árbol vigente); esta
    clase es una copia idéntica y sin dependencias, para que `objectives` siga
    siendo un dominio aislado (no importa nada de `eon.core`, `eon.tools` ni
    de ningún proveedor LLM, según exige OBJECTIVES.md §0).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event_name: str, callback: Callable) -> None:
        self._subscribers[event_name].append(callback)

    def emit(self, event_name: str, **data) -> None:
        for callback in self._subscribers.get(event_name, []):
            try:
                callback(**data)
            except Exception:  # noqa: BLE001
                pass


OBJETIVO_CREADO = "objetivo_creado"
OBJETIVO_DESCOMPUESTO = "objetivo_descompuesto"
OBJETIVO_REASIGNADO = "objetivo_reasignado"
OBJETIVO_PLANIFICANDO = "objetivo_planificando"
OBJETIVO_BLOQUEADO = "objetivo_bloqueado"
OBJETIVO_DESBLOQUEADO = "objetivo_desbloqueado"
OBJETIVO_INICIADO = "objetivo_iniciado"
OBJETIVO_PAUSADO = "objetivo_pausado"
OBJETIVO_REANUDADO = "objetivo_reanudado"
OBJETIVO_VERIFICANDO = "objetivo_verificando"
OBJETIVO_COMPLETADO = "objetivo_completado"
OBJETIVO_REINTENTANDO = "objetivo_reintentando"
OBJETIVO_FALLIDO = "objetivo_fallido"
OBJETIVO_FALLANDO = "objetivo_fallando"  # ver nota arriba
OBJETIVO_CANCELADO = "objetivo_cancelado"

TODOS = frozenset(
    {
        OBJETIVO_CREADO,
        OBJETIVO_DESCOMPUESTO,
        OBJETIVO_REASIGNADO,
        OBJETIVO_PLANIFICANDO,
        OBJETIVO_BLOQUEADO,
        OBJETIVO_DESBLOQUEADO,
        OBJETIVO_INICIADO,
        OBJETIVO_PAUSADO,
        OBJETIVO_REANUDADO,
        OBJETIVO_VERIFICANDO,
        OBJETIVO_COMPLETADO,
        OBJETIVO_REINTENTANDO,
        OBJETIVO_FALLIDO,
        OBJETIVO_FALLANDO,
        OBJETIVO_CANCELADO,
    }
)
