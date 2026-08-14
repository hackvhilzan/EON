"""Catálogo de eventos publicados por Workspace (Fase 11).

Exactamente los once eventos de WORKSPACE.md §7 -- ninguno más. Ninguna
transición de la máquina de estados de §3, ni las operaciones de ciclo de
vida (`abrir`, `cerrar`, `eliminar`), ocurre sin emitir su evento
correspondiente en el mismo acto en que se anota en `historial`.
"""

from __future__ import annotations

WORKSPACE_CREADO = "workspace_creado"
WORKSPACE_PLANIFICANDO = "workspace_planificando"
WORKSPACE_SCHEDULING = "workspace_scheduling"
WORKSPACE_EJECUTANDO = "workspace_ejecutando"
WORKSPACE_VERIFICANDO = "workspace_verificando"
WORKSPACE_COMPLETADO = "workspace_completado"
WORKSPACE_FALLIDO = "workspace_fallido"
WORKSPACE_CANCELADO = "workspace_cancelado"
WORKSPACE_ABIERTO = "workspace_abierto"
WORKSPACE_CERRADO = "workspace_cerrado"
WORKSPACE_ELIMINADO = "workspace_eliminado"

TODOS = frozenset(
    {
        WORKSPACE_CREADO,
        WORKSPACE_PLANIFICANDO,
        WORKSPACE_SCHEDULING,
        WORKSPACE_EJECUTANDO,
        WORKSPACE_VERIFICANDO,
        WORKSPACE_COMPLETADO,
        WORKSPACE_FALLIDO,
        WORKSPACE_CANCELADO,
        WORKSPACE_ABIERTO,
        WORKSPACE_CERRADO,
        WORKSPACE_ELIMINADO,
    }
)
