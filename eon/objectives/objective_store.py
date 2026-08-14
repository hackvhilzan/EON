"""
eon.objectives.objective_store
=================================
Persistencia del dominio de Objetivos. Sin lógica de negocio: crear, leer,
actualizar, listar. Nada de validar transiciones, ciclos ni invariantes -- todo
eso vive en `state_machine`, `dependency_graph` y `ObjectiveManager`. Mismo
espíritu que `ToolRegistry`/`CapabilityRegistry`: el registro es tonto, la
inteligencia vive un nivel arriba (igual que `KnowledgeStore` separa
persistencia de las 4 leyes del genoma, que viven en `eon.core.genome`).

Backend por defecto: en memoria (`InMemoryObjectiveStore`). Fuera de alcance de
OBJECTIVES.md §11 decidir el backend de producción -- cualquier otro backend
(Postgres, el mismo patrón de archivo JSON que `KnowledgeStore`, etc.) solo
necesita implementar esta misma interfaz `ObjectiveStore`.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from .exceptions import ObjectiveNotFoundError
from .models import Objective


class ObjectiveStore(ABC):
    """Interfaz mínima que debe cumplir cualquier backend de persistencia de
    Objetivos."""

    @abstractmethod
    def create(self, objective: Objective) -> Objective: ...

    @abstractmethod
    def get(self, objective_id: str) -> Objective | None: ...

    @abstractmethod
    def update(self, objective: Objective) -> Objective: ...

    @abstractmethod
    def list(self) -> list[Objective]: ...

    @abstractmethod
    def children(self, objective_id: str) -> list[Objective]: ...

    @abstractmethod
    def root_objectives(self) -> list[Objective]: ...


class InMemoryObjectiveStore(ObjectiveStore):
    """Backend en memoria, con lock para uso concurrente básico (mismo patrón que
    `KnowledgeStore`/`MemoryStore` de `eon.core`)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._objectives: dict[str, Objective] = {}

    def create(self, objective: Objective) -> Objective:
        with self._lock:
            if objective.id in self._objectives:
                raise ValueError(f"Ya existe un Objetivo con id '{objective.id}'.")
            self._objectives[objective.id] = objective
        return objective

    def get(self, objective_id: str) -> Objective | None:
        return self._objectives.get(objective_id)

    def update(self, objective: Objective) -> Objective:
        """No valida nada -- asume que quien llama (ObjectiveManager) ya pasó por
        `state_machine.validar_transicion()` o el chequeo que corresponda. El
        Store solo persiste lo que ya se decidió que es legal."""
        with self._lock:
            if objective.id not in self._objectives:
                raise ObjectiveNotFoundError(f"Objetivo no encontrado: '{objective.id}'.")
            self._objectives[objective.id] = objective
        return objective

    def list(self) -> list[Objective]:
        return list(self._objectives.values())

    def children(self, objective_id: str) -> list[Objective]:
        return [o for o in self._objectives.values() if o.padre_id == objective_id]

    def root_objectives(self) -> list[Objective]:
        return [o for o in self._objectives.values() if o.padre_id is None]
