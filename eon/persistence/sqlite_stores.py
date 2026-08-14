"""
eon.persistence.sqlite_stores
================================
Implementaciones SQLite de los 7 stores de dominio de EON.

Cada store implementa la misma interfaz ABC que su contraparte InMemory,
de forma que son intercambiables sin tocar el Manager correspondiente.
"""

from __future__ import annotations

import threading
from typing import Any

from .sqlite_engine import SQLiteEngine

# ─── ObjectiveStore ──────────────────────────────────────


class SQLiteObjectiveStore:
    """SQLite backend para ObjectiveStore."""

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def create(self, objective: Any) -> Any:
        d = objective.to_dict()
        padre_id = d.get("padre_id")
        estado = d.get("estado", "pendiente")
        self._engine.insert_entity("objectives", objective.id, d, {"padre_id": padre_id, "estado": estado})
        return objective

    def get(self, objective_id: str) -> Any:
        from ..objectives.models import Objective

        d = self._engine.get_entity("objectives", objective_id)
        return Objective.from_dict(d) if d else None

    def update(self, objective: Any) -> Any:
        d = objective.to_dict()
        padre_id = d.get("padre_id")
        estado = d.get("estado", "pendiente")
        self._engine.upsert_entity("objectives", objective.id, d, {"padre_id": padre_id, "estado": estado})
        return objective

    def list(self) -> list[Any]:
        from ..objectives.models import Objective

        return [Objective.from_dict(d) for d in self._engine.list_entities("objectives")]

    def children(self, objective_id: str) -> list[Any]:
        from ..objectives.models import Objective

        rows = self._engine.list_entities_by_col("objectives", "padre_id", objective_id)
        return [Objective.from_dict(d) for d in rows]

    def root_objectives(self) -> list[Any]:
        from ..objectives.models import Objective

        rows = self._engine.query_all(
            "SELECT data FROM objectives WHERE padre_id IS NULL OR padre_id = '' ORDER BY created_at"
        )
        import json

        return [Objective.from_dict(json.loads(r["data"])) for r in rows]


# ─── PlannerStore ────────────────────────────────────────


class SQLitePlannerStore:
    """SQLite backend para PlannerStore."""

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def create(self, plan: Any) -> Any:
        d = plan.to_dict()
        obj_id = d.get("objective_id", "")
        estado = d.get("estado", "creado")
        version = d.get("version", 1)
        self._engine.insert_entity("plans", plan.id, d, {"objective_id": obj_id, "estado": estado, "version": version})
        return plan

    def get(self, plan_id: str) -> Any:
        from ..planner.models import Plan

        d = self._engine.get_entity("plans", plan_id)
        return Plan.from_dict(d) if d else None

    def update(self, plan: Any) -> Any:
        d = plan.to_dict()
        obj_id = d.get("objective_id", "")
        estado = d.get("estado", "creado")
        version = d.get("version", 1)
        self._engine.upsert_entity("plans", plan.id, d, {"objective_id": obj_id, "estado": estado, "version": version})
        return plan

    def list(self) -> list[Any]:
        from ..planner.models import Plan

        return [Plan.from_dict(d) for d in self._engine.list_entities("plans")]

    def by_objective(self, objective_id: str) -> list[Any]:
        from ..planner.models import Plan

        rows = self._engine.list_entities_by_col("plans", "objective_id", objective_id)
        return [Plan.from_dict(d) for d in rows]

    def active_for_objective(self, objective_id: str) -> Any:
        from ..planner.models import Plan, PlanState

        rows = self._engine.query_all(
            "SELECT data FROM plans WHERE objective_id = ? AND estado = ? ORDER BY version DESC LIMIT 1",
            (objective_id, PlanState.ACTIVO.value),
        )
        if not rows:
            return None
        import json

        return Plan.from_dict(json.loads(rows[0]["data"]))


# ─── SchedulerStore ──────────────────────────────────────


class SQLiteSchedulerStore:
    """SQLite backend para SchedulerStore."""

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def crear(self, run: Any) -> Any:
        d = run.to_dict()
        self._engine.insert_entity("scheduler_runs", run.plan_id, d, id_col="plan_id")
        # Indexar tasks
        for task_id in run.tasks:
            self._engine.execute(
                "INSERT OR IGNORE INTO scheduler_task_index (task_id, plan_id) VALUES (?, ?)",
                (task_id, run.plan_id),
            )
        return run

    def obtener(self, plan_id: str) -> Any:
        from ..scheduler.models import SchedulerRun

        d = self._engine.get_entity_by_col("scheduler_runs", "plan_id", plan_id)
        if d is None:
            from ..scheduler.exceptions import SchedulerNotFoundError

            raise SchedulerNotFoundError(plan_id)
        return SchedulerRun.from_dict(d)

    def guardar(self, run: Any) -> Any:
        d = run.to_dict()
        self._engine.upsert_entity("scheduler_runs", run.plan_id, d, id_col="plan_id")
        return run

    def listar(self) -> list[Any]:
        from ..scheduler.models import SchedulerRun

        rows = self._engine.query_all("SELECT data FROM scheduler_runs ORDER BY created_at")
        import json

        return [SchedulerRun.from_dict(json.loads(r["data"])) for r in rows]

    def plan_de_task(self, task_id: str) -> str:
        row = self._engine.query_one("SELECT plan_id FROM scheduler_task_index WHERE task_id = ?", (task_id,))
        if row is None:
            from ..scheduler.exceptions import TaskNotFoundError

            raise TaskNotFoundError(task_id)
        return row["plan_id"]

    def plan_de_task_o_none(self, task_id: str) -> str | None:
        row = self._engine.query_one("SELECT plan_id FROM scheduler_task_index WHERE task_id = ?", (task_id,))
        return row["plan_id"] if row else None


# ─── WorkspaceStore ──────────────────────────────────────


class SQLiteWorkspaceStore:
    """SQLite backend para WorkspaceStore."""

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def create(self, workspace: Any) -> Any:
        d = workspace.to_dict()
        obj_id = d.get("objective_id", "")
        estado = d.get("estado", "created")
        self._engine.insert_entity("workspaces", workspace.id, d, {"objective_id": obj_id, "estado": estado})
        return workspace

    def get(self, workspace_id: str) -> Any:
        from ..workspace.models import Workspace

        d = self._engine.get_entity("workspaces", workspace_id)
        return Workspace.from_dict(d) if d else None

    def update(self, workspace: Any) -> Any:
        d = workspace.to_dict()
        obj_id = d.get("objective_id", "")
        estado = d.get("estado", "created")
        self._engine.upsert_entity("workspaces", workspace.id, d, {"objective_id": obj_id, "estado": estado})
        return workspace

    def delete(self, workspace_id: str) -> None:
        deleted = self._engine.delete_entity("workspaces", workspace_id)
        if not deleted:
            from ..workspace.exceptions import WorkspaceNotFoundError

            raise WorkspaceNotFoundError(workspace_id)

    def list(self) -> list[Any]:
        from ..workspace.models import Workspace

        return [Workspace.from_dict(d) for d in self._engine.list_entities("workspaces")]


# ─── PackageStore ────────────────────────────────────────


class SQLitePackageStore:
    """SQLite backend para PackageStore."""

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def create(self, package: Any) -> Any:
        d = package.to_dict()
        ws_id = d.get("workspace_id", "")
        self._engine.insert_entity("packages", package.id, d, {"workspace_id": ws_id})
        return package

    def get(self, package_id: str) -> Any:
        from ..package.models import Package

        d = self._engine.get_entity("packages", package_id)
        return Package.from_dict(d) if d else None

    def update(self, package: Any) -> Any:
        d = package.to_dict()
        ws_id = d.get("workspace_id", "")
        self._engine.upsert_entity("packages", package.id, d, {"workspace_id": ws_id})
        return package

    def list(self) -> list[Any]:
        from ..package.models import Package

        return [Package.from_dict(d) for d in self._engine.list_entities("packages")]


# ─── WorkerStore ─────────────────────────────────────────


class SQLiteWorkerStore:
    """SQLite backend para WorkerStore."""

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def create(self, worker: Any) -> Any:
        d = worker.to_dict()
        estado = d.get("estado", "idle")
        self._engine.insert_entity("workers", worker.id, d, {"estado": estado})
        return worker

    def get(self, worker_id: str) -> Any:
        from ..workers.models import Worker

        d = self._engine.get_entity("workers", worker_id)
        return Worker.from_dict(d) if d else None

    def update(self, worker: Any) -> Any:
        d = worker.to_dict()
        estado = d.get("estado", "idle")
        self._engine.upsert_entity("workers", worker.id, d, {"estado": estado})
        return worker

    def list(self) -> list[Any]:
        from ..workers.models import Worker

        return [Worker.from_dict(d) for d in self._engine.list_entities("workers")]

    def list_idle(self) -> list[Any]:
        from ..workers.models import Worker

        rows = self._engine.list_entities_by_col("workers", "estado", "idle")
        return [Worker.from_dict(d) for d in rows]


# ─── CoordinatorStore ────────────────────────────────────


class SQLiteCoordinatorStore:
    """SQLite backend para CoordinatorStore."""

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def create(self, execution: Any) -> Any:
        d = execution.to_dict()
        self._engine.insert_entity("coordinator_executions", execution.id, d)
        return execution

    def get(self, execution_id: str) -> Any:
        from ..coordinator.models import CoordinatorExecution

        d = self._engine.get_entity("coordinator_executions", execution_id)
        return CoordinatorExecution.from_dict(d) if d else None

    def update(self, execution: Any) -> Any:
        d = execution.to_dict()
        self._engine.upsert_entity("coordinator_executions", execution.id, d)
        return execution

    def delete(self, execution_id: str) -> None:
        self._engine.delete_entity("coordinator_executions", execution_id)

    def list(self) -> list[Any]:
        from ..coordinator.models import CoordinatorExecution

        return [CoordinatorExecution.from_dict(d) for d in self._engine.list_entities("coordinator_executions")]
