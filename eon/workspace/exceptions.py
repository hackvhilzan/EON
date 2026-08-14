"""Excepciones del dominio Workspace (Fase 11)."""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base de todas las excepciones de Workspace."""


class WorkspaceNotFoundError(WorkspaceError):
    """No existe un Workspace con ese `workspace_id`."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        super().__init__(f"No existe el workspace workspace_id={workspace_id!r}")


class WorkspaceAlreadyExistsError(WorkspaceError):
    """El Objetivo raíz ya posee un Workspace no terminal (WORKSPACE.md
    §2, §9.2, Invariante 2). Un Objetivo raíz solo puede tener un Workspace
    nuevo una vez el anterior alcanzó un estado terminal, y siempre bajo un
    `objective_id` distinto (reintento, OBJECTIVES.md §2.3)."""

    def __init__(self, objective_id: str) -> None:
        self.objective_id = objective_id
        super().__init__(f"El objetivo objective_id={objective_id!r} ya posee un Workspace no terminal.")


class InvalidWorkspaceError(WorkspaceError):
    """El Workspace no cumple los invariantes mínimos (p. ej. sin
    `objective_id`)."""


class IllegalWorkspaceTransitionError(WorkspaceError):
    """Transición de WorkspaceState no contemplada en WORKSPACE.md §3."""


class WorkspaceNotTerminalError(WorkspaceError):
    """Se intentó `eliminar()` un Workspace en un estado no terminal
    (WORKSPACE.md §9.4, Invariante 15)."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        super().__init__(f"El workspace {workspace_id!r} no está en un estado terminal: no puede eliminarse.")


class WorkspacePathEscapeError(WorkspaceError):
    """Una ruta resuelta por `WorkspaceLayout` intenta escapar del `root`
    del Workspace, o referencia una subcarpeta fuera de la estructura fija
    de §5 (WORKSPACE.md §9.3)."""


class WorkspaceRecoveryError(WorkspaceError):
    """La estructura de directorios de §5 no existe o está incompleta:
    `recuperar()` no puede continuar (WORKSPACE.md §8.2, paso 1)."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        super().__init__(f"La estructura de directorios del workspace {workspace_id!r} no existe o está incompleta.")


class ArtifactOwnershipError(WorkspaceError):
    """Un Worker intentó escribir o eliminar un artefacto que pertenece a
    otro Worker, sin autorización explícita de la Task (WORKSPACE.md §5.1,
    Invariante 10)."""

    def __init__(self, ruta_relativa: str, propietario: str, intento: str) -> None:
        self.ruta_relativa = ruta_relativa
        self.propietario = propietario
        self.intento = intento
        super().__init__(
            f"El artefacto {ruta_relativa!r} pertenece al worker {propietario!r}; "
            f"el worker {intento!r} no está autorizado a modificarlo o eliminarlo."
        )
