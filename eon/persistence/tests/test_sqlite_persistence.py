"""
Tests de persistencia SQLite para Fase 1.

Patrón: create → close → reopen → verify
Verifica que el estado sobrevive un reinicio de proceso.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eon.coordinator.models import (
    CoordinatorExecution,
)
from eon.objectives.models import Objective, ObjectiveOrigin, ObjectiveState
from eon.package.models import Package
from eon.persistence import create_stores
from eon.planner.models import Plan, PlanState
from eon.planner.task import Task
from eon.scheduler.models import (
    SchedulerRun,
    SchedulerState,
    TaskExecutionRecord,
    TaskExecutionState,
)
from eon.workers.models import Worker, WorkerState
from eon.workspace.models import Workspace

# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_eon.db"


@pytest.fixture
def stores(tmp_db: Path):
    registry = create_stores(backend="sqlite", db_path=str(tmp_db))
    yield registry
    registry.close()


# ─── Objective Store ───────────────────────────────────────


class TestSQLiteObjectiveStore:
    def test_create_and_get(self, stores):
        obj = Objective(
            descripcion="Construir API REST",
            criterio_de_exito="200 OK en /health",
            origen=ObjectiveOrigin.USUARIO,
        )
        stores.objective_store.create(obj)
        fetched = stores.objective_store.get(obj.id)
        assert fetched is not None
        assert fetched.descripcion == "Construir API REST"
        assert fetched.estado == ObjectiveState.PENDIENTE

    def test_survives_reopen(self, tmp_db: Path):
        # Create
        registry1 = create_stores(backend="sqlite", db_path=str(tmp_db))
        obj = Objective(
            descripcion="Objetivo persistente",
            criterio_de_exito="Criterio",
            origen=ObjectiveOrigin.USUARIO,
        )
        registry1.objective_store.create(obj)
        registry1.close()

        # Reopen
        registry2 = create_stores(backend="sqlite", db_path=str(tmp_db))
        fetched = registry2.objective_store.get(obj.id)
        assert fetched is not None
        assert fetched.descripcion == "Objetivo persistente"
        registry2.close()

    def test_update(self, stores):
        obj = Objective(
            descripcion="Original",
            criterio_de_exito="OK",
            origen=ObjectiveOrigin.USUARIO,
        )
        stores.objective_store.create(obj)
        obj.estado = ObjectiveState.EN_PROGRESO
        stores.objective_store.update(obj)
        fetched = stores.objective_store.get(obj.id)
        assert fetched.estado == ObjectiveState.EN_PROGRESO

    def test_list(self, stores):
        for i in range(3):
            stores.objective_store.create(
                Objective(
                    descripcion=f"Obj {i}",
                    criterio_de_exito="OK",
                    origen=ObjectiveOrigin.USUARIO,
                )
            )
        all_objs = stores.objective_store.list()
        assert len(all_objs) == 3

    def test_children_and_root(self, stores):
        parent = Objective(
            descripcion="Parent",
            criterio_de_exito="OK",
            origen=ObjectiveOrigin.USUARIO,
        )
        stores.objective_store.create(parent)
        child = Objective(
            descripcion="Child",
            criterio_de_exito="OK",
            origen=ObjectiveOrigin.USUARIO,
            padre_id=parent.id,
        )
        stores.objective_store.create(child)
        children = stores.objective_store.children(parent.id)
        assert len(children) == 1
        assert children[0].id == child.id
        roots = stores.objective_store.root_objectives()
        assert len(roots) == 1
        assert roots[0].id == parent.id

    def test_historial_roundtrip(self, stores):
        obj = Objective(
            descripcion="Con historial",
            criterio_de_exito="OK",
            origen=ObjectiveOrigin.USUARIO,
        )
        obj.registrar_transicion("pendiente", "en_progreso", "inicio")
        stores.objective_store.create(obj)
        fetched = stores.objective_store.get(obj.id)
        assert len(fetched.historial) == 1
        assert fetched.historial[0]["evento"] == "inicio"


# ─── Planner Store ─────────────────────────────────────────


class TestSQLitePlannerStore:
    def test_create_and_get(self, stores):
        task = Task(capability_id="code.write", id="t1")
        plan = Plan(objective_id="obj-1", tasks=[task])
        stores.planner_store.create(plan)
        fetched = stores.planner_store.get(plan.id)
        assert fetched is not None
        assert fetched.objective_id == "obj-1"
        assert len(fetched.tasks) == 1
        assert fetched.tasks[0].capability_id == "code.write"

    def test_survives_reopen(self, tmp_db: Path):
        registry1 = create_stores(backend="sqlite", db_path=str(tmp_db))
        task = Task(capability_id="test.run", id="t1")
        plan = Plan(objective_id="obj-x", tasks=[task])
        registry1.planner_store.create(plan)
        registry1.close()

        registry2 = create_stores(backend="sqlite", db_path=str(tmp_db))
        fetched = registry2.planner_store.get(plan.id)
        assert fetched is not None
        assert fetched.tasks[0].capability_id == "test.run"
        registry2.close()

    def test_by_objective(self, stores):
        task = Task(capability_id="x", id="t1")
        plan = Plan(objective_id="obj-a", tasks=[task])
        stores.planner_store.create(plan)
        results = stores.planner_store.by_objective("obj-a")
        assert len(results) == 1

    def test_update_state(self, stores):
        task = Task(capability_id="x", id="t1")
        plan = Plan(objective_id="obj-u", tasks=[task])
        stores.planner_store.create(plan)
        plan.estado = PlanState.ACTIVO
        stores.planner_store.update(plan)
        fetched = stores.planner_store.get(plan.id)
        assert fetched.estado == PlanState.ACTIVO


# ─── Scheduler Store ───────────────────────────────────────


class TestSQLiteSchedulerStore:
    def test_crear_and_obtener(self, stores):
        run = SchedulerRun(
            plan_id="plan-1",
            tasks={"t1": TaskExecutionRecord(task_id="t1")},
            orden=("t1",),
        )
        stores.scheduler_store.crear(run)
        fetched = stores.scheduler_store.obtener("plan-1")
        assert fetched.plan_id == "plan-1"
        assert "t1" in fetched.tasks

    def test_survives_reopen(self, tmp_db: Path):
        registry1 = create_stores(backend="sqlite", db_path=str(tmp_db))
        run = SchedulerRun(
            plan_id="plan-r",
            tasks={"t1": TaskExecutionRecord(task_id="t1", estado=TaskExecutionState.RUNNING)},
            orden=("t1",),
        )
        registry1.scheduler_store.crear(run)
        registry1.close()

        registry2 = create_stores(backend="sqlite", db_path=str(tmp_db))
        fetched = registry2.scheduler_store.obtener("plan-r")
        assert fetched.tasks["t1"].estado == TaskExecutionState.RUNNING
        registry2.close()

    def test_plan_de_task(self, stores):
        run = SchedulerRun(
            plan_id="plan-idx",
            tasks={"task-99": TaskExecutionRecord(task_id="task-99")},
            orden=("task-99",),
        )
        stores.scheduler_store.crear(run)
        assert stores.scheduler_store.plan_de_task("task-99") == "plan-idx"

    def test_plan_de_task_o_none(self, stores):
        assert stores.scheduler_store.plan_de_task_o_none("nonexistent") is None

    def test_guardar(self, stores):
        run = SchedulerRun(
            plan_id="plan-g",
            tasks={"t1": TaskExecutionRecord(task_id="t1")},
            orden=("t1",),
        )
        stores.scheduler_store.crear(run)
        run.estado = SchedulerState.RUNNING
        stores.scheduler_store.guardar(run)
        fetched = stores.scheduler_store.obtener("plan-g")
        assert fetched.estado == SchedulerState.RUNNING


# ─── Workspace Store ───────────────────────────────────────


class TestSQLiteWorkspaceStore:
    def test_create_and_get(self, stores):
        ws = Workspace(objective_id="obj-1")
        stores.workspace_store.create(ws)
        fetched = stores.workspace_store.get(ws.id)
        assert fetched is not None
        assert fetched.objective_id == "obj-1"

    def test_survives_reopen(self, tmp_db: Path):
        registry1 = create_stores(backend="sqlite", db_path=str(tmp_db))
        ws = Workspace(objective_id="obj-ws")
        registry1.workspace_store.create(ws)
        registry1.close()

        registry2 = create_stores(backend="sqlite", db_path=str(tmp_db))
        fetched = registry2.workspace_store.get(ws.id)
        assert fetched is not None
        assert fetched.objective_id == "obj-ws"
        registry2.close()

    def test_delete(self, stores):
        ws = Workspace(objective_id="obj-d")
        stores.workspace_store.create(ws)
        stores.workspace_store.delete(ws.id)
        assert stores.workspace_store.get(ws.id) is None


# ─── Package Store ─────────────────────────────────────────


class TestSQLitePackageStore:
    def test_create_and_get(self, stores):
        pkg = Package(workspace_id="ws-1")
        stores.package_store.create(pkg)
        fetched = stores.package_store.get(pkg.id)
        assert fetched is not None
        assert fetched.workspace_id == "ws-1"

    def test_survives_reopen(self, tmp_db: Path):
        registry1 = create_stores(backend="sqlite", db_path=str(tmp_db))
        pkg = Package(workspace_id="ws-pkg")
        registry1.package_store.create(pkg)
        registry1.close()

        registry2 = create_stores(backend="sqlite", db_path=str(tmp_db))
        fetched = registry2.package_store.get(pkg.id)
        assert fetched is not None
        assert fetched.workspace_id == "ws-pkg"
        registry2.close()


# ─── Worker Store ──────────────────────────────────────────


class TestSQLiteWorkerStore:
    def test_create_and_get(self, stores):
        worker = Worker(capabilities=("code.write",), nombre="w1")
        stores.worker_store.create(worker)
        fetched = stores.worker_store.get(worker.id)
        assert fetched is not None
        assert "code.write" in fetched.capabilities

    def test_survives_reopen(self, tmp_db: Path):
        registry1 = create_stores(backend="sqlite", db_path=str(tmp_db))
        worker = Worker(capabilities=("test.run",), nombre="persistent-worker")
        registry1.worker_store.create(worker)
        registry1.close()

        registry2 = create_stores(backend="sqlite", db_path=str(tmp_db))
        fetched = registry2.worker_store.get(worker.id)
        assert fetched is not None
        assert fetched.nombre == "persistent-worker"
        registry2.close()

    def test_list_idle(self, stores):
        w1 = Worker(capabilities=("x",), nombre="idle1")
        w2 = Worker(capabilities=("x",), nombre="busy1")
        w2.estado = WorkerState.BUSY
        stores.worker_store.create(w1)
        stores.worker_store.create(w2)
        idle = stores.worker_store.list_idle()
        assert len(idle) == 1
        assert idle[0].nombre == "idle1"


# ─── Coordinator Store ─────────────────────────────────────


class TestSQLiteCoordinatorStore:
    def test_create_and_get(self, stores):
        exec_model = CoordinatorExecution(
            objective_id="obj-1",
            plan_id="plan-1",
            workspace_id="ws-1",
            package_id="pkg-1",
        )
        stores.coordinator_store.create(exec_model)
        fetched = stores.coordinator_store.get(exec_model.id)
        assert fetched is not None
        assert fetched.objective_id == "obj-1"

    def test_survives_reopen(self, tmp_db: Path):
        registry1 = create_stores(backend="sqlite", db_path=str(tmp_db))
        exec_model = CoordinatorExecution(
            objective_id="obj-c",
            plan_id="plan-c",
            workspace_id="ws-c",
            package_id="pkg-c",
        )
        registry1.coordinator_store.create(exec_model)
        registry1.close()

        registry2 = create_stores(backend="sqlite", db_path=str(tmp_db))
        fetched = registry2.coordinator_store.get(exec_model.id)
        assert fetched is not None
        assert fetched.objective_id == "obj-c"
        registry2.close()


# ─── EventStore ────────────────────────────────────────────


class TestEventStore:
    def test_append_and_get(self, stores):
        stores.event_store.append("exec-1", "objective_created", {"id": "obj-1"})
        events = stores.event_store.get_events("exec-1")
        assert len(events) == 1
        assert events[0].event_type == "objective_created"
        assert events[0].payload["id"] == "obj-1"

    def test_events_survive_reopen(self, tmp_db: Path):
        registry1 = create_stores(backend="sqlite", db_path=str(tmp_db))
        registry1.event_store.append("exec-r", "plan_created", {"plan_id": "p1"})
        registry1.close()

        registry2 = create_stores(backend="sqlite", db_path=str(tmp_db))
        events = registry2.event_store.get_events("exec-r")
        assert len(events) == 1
        assert events[0].payload["plan_id"] == "p1"
        registry2.close()

    def test_get_all_events(self, stores):
        stores.event_store.append("e1", "type_a", {"x": 1})
        stores.event_store.append("e2", "type_b", {"y": 2})
        all_events = stores.event_store.get_all_events()
        assert len(all_events) == 2

    def test_filter_by_type(self, stores):
        stores.event_store.append("e1", "type_a", {})
        stores.event_store.append("e1", "type_b", {})
        stores.event_store.append("e1", "type_a", {})
        type_a = stores.event_store.get_events("e1", event_type="type_a")
        assert len(type_a) == 2

    def test_sequence_ordering(self, stores):
        for i in range(5):
            stores.event_store.append("exec", "event", {"n": i})
        events = stores.event_store.get_events("exec")
        seqs = [e.seq for e in events]
        assert seqs == sorted(seqs)

    def test_count(self, stores):
        stores.event_store.append("e1", "type_a", {})
        stores.event_store.append("e2", "type_b", {})
        assert stores.event_store.count() == 2


# ─── Integration: KernelRuntime with SQLite ────────────────


class TestKernelRuntimeSQLite:
    def test_runtime_with_sqlite(self, tmp_path: Path):
        from eon.planner.task import Task
        from eon.runtime import KernelRuntime

        db_path = str(tmp_path / "kernel_eon.db")
        kernel = KernelRuntime(
            root=str(tmp_path / "runtime"),
            tasks=[Task(capability_id="test.dummy", id="t1")],
            persistence_backend="sqlite",
            db_path=db_path,
        )
        result = kernel.run("Test objective", "Success criteria")
        assert result.execution_id is not None
        # Verify it was persisted
        execution = kernel.recuperar_ejecucion(result.execution_id)
        assert execution is not None
        kernel.close()

    def test_runtime_survives_reopen(self, tmp_path: Path):
        from eon.planner.task import Task
        from eon.runtime import KernelRuntime

        db_path = str(tmp_path / "kernel_reopen.db")
        runtime_root = str(tmp_path / "runtime_reopen")

        # First run
        kernel1 = KernelRuntime(
            root=runtime_root,
            tasks=[Task(capability_id="test.dummy", id="t1")],
            persistence_backend="sqlite",
            db_path=db_path,
        )
        result = kernel1.run("Persisted objective", "Done")
        exec_id = result.execution_id
        kernel1.close()

        # Reopen
        kernel2 = KernelRuntime(
            root=runtime_root,
            tasks=[Task(capability_id="test.dummy", id="t1")],
            persistence_backend="sqlite",
            db_path=db_path,
        )
        execution = kernel2.recuperar_ejecucion(exec_id)
        assert execution is not None
        executions = kernel2.listar_ejecuciones()
        assert len(executions) >= 1
        kernel2.close()
