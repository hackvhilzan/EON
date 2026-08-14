"""
eon.planner.planner_exceptions
=================================
Excepciones propias del Planner Engine. Separadas de las de eon.objectives,
eon.capabilities y eon.tools por el mismo motivo que allí: quien captura
errores de Plan no debería tener que saber nada de Objetivos, Capacidades,
Tools ni proveedores LLM concretos (PLANNER.md §0, Invariante 13).
"""

from __future__ import annotations


class PlannerError(Exception):
    """Base de todas las excepciones del Planner Engine."""


class PlanNotFoundError(PlannerError):
    """Se pidió un Plan que no existe en el PlannerStore."""


class InvalidPlanError(PlannerError):
    """El Plan no cumple los invariantes mínimos del contrato (p.ej. una Task
    sin capability_id, o una lista de tasks vacía -- PLANNER.md §4)."""


class IllegalPlanTransitionError(PlannerError):
    """Se intentó una transición de estado no contemplada en la máquina de
    estados de PLANNER.md §8, o una operación sobre un estado que no la
    admite (p.ej. activar un Plan ya cancelado)."""


class TerminalPlanError(PlannerError):
    """Se intentó modificar un Plan que ya está en un estado terminal
    (cancelado) -- un Plan cancelado nunca vuelve atrás (PLANNER.md §8)."""


class InvalidTaskGraphError(PlannerError):
    """El orden total de Tasks (PLANNER.md Invariante 8) es inconsistente --
    p.ej. una dependencia entre Tasks que no existe en el propio Plan, o un
    ciclo entre Tasks."""
