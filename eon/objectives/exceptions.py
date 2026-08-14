"""
eon.objectives.exceptions
============================
Excepciones propias del Objective Engine. Separadas de las de eon.capabilities y
eon.tools por el mismo motivo que allí: quien captura errores de Objetivos no
debería tener que saber nada de Capacidades, Tools ni proveedores LLM concretos
(Invariante 13 de OBJECTIVES.md).
"""

from __future__ import annotations


class ObjectiveError(Exception):
    """Base de todas las excepciones del Objective Engine."""


class ObjectiveNotFoundError(ObjectiveError):
    """Se pidió un Objetivo que no existe en el ObjectiveStore."""


class InvalidObjectiveError(ObjectiveError):
    """El Objetivo no cumple los invariantes mínimos del contrato (p.ej. nace sin
    criterio_de_exito -- Invariante 1)."""


class IllegalTransitionError(ObjectiveError):
    """Se intentó una transición de estado no contemplada en la máquina de estados
    de OBJECTIVES.md §5, o una operación sobre un estado que no la admite (p.ej.
    cancelar algo ya terminal)."""


class TerminalObjectiveError(ObjectiveError):
    """Se intentó modificar un Objetivo que ya está en un estado terminal
    (completado, fallido o cancelado) -- Invariante 2: no revierten, no se
    reabren, no se les cambia el propietario ni se les añaden sub-objetivos."""


class CyclicDependencyError(ObjectiveError):
    """La dependencia declarada (depende_de o padre_id) crearía un ciclo. Se
    rechaza siempre al declararse, nunca se tolera en tiempo de ejecución
    (Invariante 5)."""


class InvalidHierarchyError(ObjectiveError):
    """Violación del invariante de árbol: un Objetivo tiene como máximo un
    padre_id (Invariante 10) -- p.ej. intentar reparentar uno existente."""


class InvalidDecompositionError(ObjectiveError):
    """La descomposición viola una regla del contrato -- p.ej. intentar
    descomponer un Objetivo que ya se resuelve con un plan propio de Task
    (Invariante 4: nunca ambas cosas a la vez)."""


class RetryLimitExceededError(ObjectiveError):
    """Se pidió reintentar un Objetivo que ya agotó su límite de reintentos
    (OBJECTIVES.md §11: "no se fija aquí cuántos, solo que exista un
    límite"). `ObjectiveManager.reintentar()` la lanza -- tras dejar el
    Objetivo en `fallido`, que es la transición de dominio que corresponde
    (§5) -- para que quien orquesta el ciclo (Coordinator) reciba una señal
    explícita en vez de tener que releer el estado para saber si el
    reintento se concedió."""

    def __init__(self, objective_id: str) -> None:
        self.objective_id = objective_id
        super().__init__(f"El objetivo {objective_id!r} agotó su límite de reintentos.")
