"""Catálogo de eventos publicados por Package (Fase 12)."""

from __future__ import annotations

PACKAGE_SOLICITADO = "package_solicitado"
PACKAGE_CONSTRUYENDO = "package_construyendo"
PACKAGE_LISTO = "package_listo"
PACKAGE_FALLIDO = "package_fallido"
PACKAGE_CANCELADO = "package_cancelado"

TODOS = frozenset(
    {
        PACKAGE_SOLICITADO,
        PACKAGE_CONSTRUYENDO,
        PACKAGE_LISTO,
        PACKAGE_FALLIDO,
        PACKAGE_CANCELADO,
    }
)
