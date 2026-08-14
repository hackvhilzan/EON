"""Excepciones del dominio Coordinator (Fase 13, ORDEN MAESTRA v1.0)."""

from __future__ import annotations


class CoordinatorError(Exception):
    """Base de todas las excepciones de Coordinator."""


class CoordinatorNotFoundError(CoordinatorError):
    """No existe una ejecución con ese `execution_id`."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(f"No existe la ejecución execution_id={execution_id!r}")


class IllegalCoordinatorTransitionError(CoordinatorError):
    """Transición de CoordinatorState no contemplada (ORDEN MAESTRA §ORDEN DE
    EJECUCIÓN). Incluye cualquier salida desde un estado terminal
    (`COMPLETED`/`FAILED`/`CANCELLED`)."""


class CoordinatorImmutableError(CoordinatorError):
    """Se intentó mutar una ejecución que ya alcanzó un estado terminal.
    Mismo criterio que `PackageImmutableError` (PACKAGE.md Invariante 3):
    terminal implica inmutable en su totalidad."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(f"La ejecución {execution_id!r} es terminal: es inmutable en su totalidad.")


class CoordinatorRecoveryError(CoordinatorError):
    """La estructura de directorios no existe o está incompleta, o el
    estado persistido es inconsistente: `recuperar()` no puede continuar."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(
            f"La estructura o el estado de la ejecución {execution_id!r} no existe, está incompleta o es inconsistente."
        )


class CoordinatorPathEscapeError(CoordinatorError):
    """Una ruta resuelta por `CoordinatorLayout` intenta escapar del `root`
    de la ejecución. Mismo criterio que `PackagePathEscapeError`."""


class DelegationError(CoordinatorError):
    """Un módulo delegado (Objectives, Planner, Scheduler, Verifier,
    Workspace o Package) devolvió una respuesta que no cumple el contrato
    esperado por el Coordinator, o lanzó un error de dominio propio que el
    Coordinator no puede interpretar. El Coordinator nunca reinterpreta
    ni oculta este error: lo traduce a una transición `FAILED`/`CANCELLED`
    de la ejecución y lo re-lanza envuelto para preservar la causa."""

    def __init__(self, modulo: str, operacion: str, causa: BaseException) -> None:
        self.modulo = modulo
        self.operacion = operacion
        self.causa = causa
        super().__init__(f"Fallo delegando en {modulo}.{operacion}(): {causa!r}")


class RetriesExhaustedError(CoordinatorError):
    """Señal de dominio de Objectives (OBJECTIVES.md §5, §9): el Objetivo
    agotó su política de reintentos. El Coordinator nunca decide cuántos
    reintentos hay (eso es política de Objectives, fuera de este
    contrato, OBJECTIVES.md §11) — solo reacciona a esta señal moviendo la
    ejecución a `FAILED`. Ver ports.py y el informe (decisión de API)."""

    def __init__(self, objective_id: str) -> None:
        self.objective_id = objective_id
        super().__init__(f"El objetivo {objective_id!r} agotó sus reintentos (OBJECTIVES.md §5).")
