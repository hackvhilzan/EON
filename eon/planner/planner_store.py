"""
eon.planner.planner_store
============================
Persistencia del dominio de Plan. Sin lógica de negocio: crear, leer,
actualizar, listar, encontrar el activo. Nada de validar transiciones ni
invariantes -- todo eso vive en `PlannerManager`. Mismo espíritu que
`eon.objectives.objective_store`: el registro es tonto, la inteligencia vive
un nivel arriba.

Backend por defecto: en memoria (`InMemoryPlannerStore`). Fuera de alcance de
PLANNER.md §11 decidir el backend de producción -- cualquier otro backend solo
necesita implementar esta misma interfaz `PlannerStore`.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from .models import Plan, PlanState
from .planner_exceptions import PlanNotFoundError


class PlannerStore(ABC):
    """Interfaz mínima que debe cumplir cualquier backend de persistencia de
    Planes."""

    @abstractmethod
    def create(self, plan: Plan) -> Plan: ...

    @abstractmethod
    def get(self, plan_id: str) -> Plan | None: ...

    @abstractmethod
    def update(self, plan: Plan) -> Plan: ...

    @abstractmethod
    def list(self) -> list[Plan]: ...

    @abstractmethod
    def by_objective(self, objective_id: str) -> list[Plan]: ...

    @abstractmethod
    def active_for_objective(self, objective_id: str) -> Plan | None: ...


class InMemoryPlannerStore(PlannerStore):
    """Backend en memoria, con lock para uso concurrente básico (mismo patrón
    que `InMemoryObjectiveStore`)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._plans: dict[str, Plan] = {}

    def create(self, plan: Plan) -> Plan:
        with self._lock:
            if plan.id in self._plans:
                raise ValueError(f"Ya existe un Plan con id '{plan.id}'.")
            self._plans[plan.id] = plan
        return plan

    def get(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)

    def update(self, plan: Plan) -> Plan:
        """No valida nada -- asume que quien llama (PlannerManager) ya pasó por
        la validación de transición que corresponda. El Store solo persiste lo
        que ya se decidió que es legal."""
        with self._lock:
            if plan.id not in self._plans:
                raise PlanNotFoundError(f"Plan no encontrado: '{plan.id}'.")
            self._plans[plan.id] = plan
        return plan

    def list(self) -> list[Plan]:
        return list(self._plans.values())

    def by_objective(self, objective_id: str) -> list[Plan]:
        return [p for p in self._plans.values() if p.objective_id == objective_id]

    def active_for_objective(self, objective_id: str) -> Plan | None:
        """PLANNER.md Invariante 2: un Objetivo posee como máximo un Plan
        activo -- si el dato en Store llegara a tener más de uno (violación de
        la Invariante 2, nunca debería ocurrir si solo se escribe vía
        PlannerManager), esto es defensivo, no normativo: devuelve el primero
        que encuentra en vez de fallar silenciosamente con uno cualquiera."""
        for plan in self._plans.values():
            if plan.objective_id == objective_id and plan.estado == PlanState.ACTIVO:
                return plan
        return None
