"""Tests de propagación del task_id y retry controlado (Fase 4 — fix).

El TaskExecutor debe propagar el `task.id` en los `parametros` que entrega
al executor personalizado, para que este pueda comportarse de forma
distinta por task — por ejemplo, fallar la primera vez y pasar la segunda,
lo que dispara el mecanismo de retry del Coordinator.

Cobertura:
- El executor recibe `task_id` en los parámetros.
- Un task que falla la primera vez y pasa la segunda produce un package
  en estado `ready` (retry exitoso).
- Un task que siempre falla produce una ejecución fallida.
- El contador de llamadas del executor refleja los retries.
- En un pipeline de 2 tasks con dependencias, el retry también funciona.

Nota importante: cuando el Coordinator reintenta, crea un NUEVO plan. El
invariante FASE9 del Scheduler exige que una Task no pertenezca a dos
planes — por eso el LLM debe generar IDs únicos en cada llamada.
"""

from __future__ import annotations

import json
import tempfile

import pytest

from eon.event_bus import EventBus
from eon.governance import (
    CapabilityPolicy,
    PolicyDecision,
    PolicyEngine,
    SandboxProfile,
)
from eon.llm.base import LLM
from eon.planner.task import Task
from eon.runtime import KernelRuntime
from eon.task_generation import (
    GovernedTaskGenerator,
    LLMTaskGenerator,
    TaskSpecValidator,
)
from eon.workers.executor import TaskExecutor

# ─── Fake LLM con IDs únicos por llamada ─────────────────────────


class UniqueFakeLLM(LLM):
    """LLM que devuelve JSON con task_ids únicos en cada llamada.
    Necesario porque el invariante FASE9 del Scheduler exige que
    una Task no pertenezca a dos planes."""

    name = "unique-fake"

    def __init__(self, num_tasks: int = 1) -> None:
        self._call = 0
        self._num_tasks = num_tasks

    def generate(self, prompt: str) -> str:
        self._call += 1
        prefix = f"r{self._call}"
        tasks = []
        for i in range(self._num_tasks):
            task = {"capability_id": "default", "id": f"{prefix}-t{i + 1}"}
            if i > 0:
                task["depende_de"] = [f"{prefix}-t{i}"]
            tasks.append(task)
        return json.dumps(tasks)


class FakeLLM(LLM):
    name = "fake"

    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, prompt: str) -> str:
        return self._response


class FakeObjective:
    def __init__(self) -> None:
        self.descripcion = "Procesar datos"
        self.criterio_de_exito = "Resultado exitoso"
        self.id = "obj-retry-1"
        self.estado = "pendiente"


def _make_engine() -> PolicyEngine:
    return PolicyEngine(
        policies=[
            CapabilityPolicy(
                policy_id="allow-default",
                capabilities={"default"},
                decision=PolicyDecision.ALLOW,
                sandbox_profile=SandboxProfile.workspace_only(),
            )
        ]
    )


def _make_gen(engine: PolicyEngine, num_tasks: int = 1) -> GovernedTaskGenerator:
    return GovernedTaskGenerator(
        inner=LLMTaskGenerator(UniqueFakeLLM(num_tasks=num_tasks), capabilities_validas=["default"]),
        validator=TaskSpecValidator(capabilities_validas=["default"]),
        policy_engine=engine,
    )


# ─── TaskExecutor unit test: propagación de task_id ─────────────


class TestTaskIdPropagation:
    """Verifica que TaskExecutor propaga task.id en los parámetros."""

    def test_executor_recibe_task_id(self):
        """El executor personalizado recibe `task_id` en los parámetros."""
        received_params: list[dict] = []

        def capturing_executor(capability_id: str, params: dict) -> bool:
            received_params.append(dict(params))
            return True

        bus = EventBus()
        executor = TaskExecutor(bus, ejecutar=capturing_executor)

        task = Task(capability_id="default", id="my-task-id", parametros={"foo": "bar"})
        executor.ejecutar("worker-1", task)

        assert len(received_params) == 1
        assert received_params[0]["task_id"] == "my-task-id"
        assert received_params[0]["foo"] == "bar"

    def test_executor_no_sobrescribe_task_id_existente(self):
        """Si el task ya tiene `task_id` en sus parámetros, no se sobrescribe."""
        received: list[dict] = []

        def capturing_executor(capability_id: str, params: dict) -> bool:
            received.append(dict(params))
            return True

        bus = EventBus()
        executor = TaskExecutor(bus, ejecutar=capturing_executor)

        task = Task(
            capability_id="default",
            id="real-id",
            parametros={"task_id": "custom-id"},
        )
        executor.ejecutar("worker-1", task)

        # setdefault respeta el valor existente
        assert received[0]["task_id"] == "custom-id"


# ─── Retry controlado: fallo primera vez, éxito la segunda ──────


class TestRetryControlado:
    """Un task que falla la primera vez y pasa la segunda debe
    producir un package en estado `ready` (retry exitoso)."""

    def test_fallo_primera_vez_exito_segunda(self):
        """El executor falla en la primera llamada global, pasa en la
        segunda. El Coordinator reintenta y la ejecución completa con
        package en estado `ready`."""
        total_calls = [0]

        def fail_first_only(capability_id: str, params: dict) -> bool:
            total_calls[0] += 1
            if total_calls[0] == 1:
                return False
            return True

        engine = _make_engine()
        gen = _make_gen(engine, num_tasks=1)

        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=fail_first_only,
            task_generation=gen,
            worker_capabilities=["default"],
            policy_engine=engine,
            max_reintentos=3,
        )

        result = runtime.run(
            descripcion="Pipeline con fallo controlado",
            criterio_de_exito="Todas las tasks completadas",
        )

        assert result.package_state == "ready"
        # El executor fue llamado al menos 2 veces (fallo + retry exitoso)
        assert total_calls[0] >= 2

    def test_fallo_siempre_produce_ejecucion_fallida(self):
        """Si el executor siempre falla, la ejecución no produce package."""

        def always_fail(capability_id: str, params: dict) -> bool:
            return False

        engine = _make_engine()
        gen = _make_gen(engine, num_tasks=1)

        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=always_fail,
            task_generation=gen,
            worker_capabilities=["default"],
            policy_engine=engine,
            max_reintentos=2,
        )

        with pytest.raises(Exception):
            runtime.run(
                descripcion="Pipeline que siempre falla",
                criterio_de_exito="Todas las tasks completadas",
            )

    def test_multiples_tasks_fallo_y_retry(self):
        """Pipeline de 2 tasks con dependencias. La primera llamada falla,
        el retry genera un nuevo plan con IDs únicos y tiene éxito.
        El resultado es un package ready y el audit log es verificable."""
        total_calls = [0]

        def fail_first_only(capability_id: str, params: dict) -> bool:
            total_calls[0] += 1
            if total_calls[0] == 1:
                return False
            return True

        engine = _make_engine()
        gen = _make_gen(engine, num_tasks=2)

        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=fail_first_only,
            task_generation=gen,
            worker_capabilities=["default"],
            policy_engine=engine,
            max_reintentos=3,
        )

        result = runtime.run(
            descripcion="Pipeline con dependencias y fallo controlado",
            criterio_de_exito="Todas las tasks completadas",
        )

        assert result.package_state == "ready"
        # El executor fue llamado al menos 3 veces (fallo t1 + retry t1 + t2)
        assert total_calls[0] >= 3
        # El audit log tiene entradas para las tasks del plan exitoso
        entries = runtime.audit_log.replay(result.execution_id)
        assert len(entries) >= 2
        assert runtime.audit_log.verificar_integridad() is True

    def test_executor_recibe_diferentes_task_ids(self):
        """En un pipeline de 2 tasks, el executor recibe task_ids distintos."""
        received_ids: list[str] = []

        def tracking_executor(capability_id: str, params: dict) -> bool:
            received_ids.append(params.get("task_id", "unknown"))
            return True

        engine = _make_engine()
        gen = _make_gen(engine, num_tasks=2)

        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=tracking_executor,
            task_generation=gen,
            worker_capabilities=["default"],
            policy_engine=engine,
        )

        runtime.run(
            descripcion="Pipeline de 2 tasks",
            criterio_de_exito="Todas completadas",
        )

        # Ambos task_ids fueron recibidos y son distintos
        assert len(received_ids) == 2
        assert received_ids[0] != received_ids[1]

    def test_reintento_agota_intentos_y_falla(self):
        """Si el executor falla todas las veces y max_reintentos=2,
        la ejecución agota los reintentos y falla sin producir package."""
        call_count = [0]

        def always_fail_n(capability_id: str, params: dict) -> bool:
            call_count[0] += 1
            return False  # siempre falla

        engine = _make_engine()
        gen = _make_gen(engine, num_tasks=1)

        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=always_fail_n,
            task_generation=gen,
            worker_capabilities=["default"],
            policy_engine=engine,
            max_reintentos=2,
        )

        with pytest.raises(Exception):
            runtime.run(
                descripcion="Pipeline que agota reintentos",
                criterio_de_exito="Todas completadas",
            )
        # Se llamó al menos 3 veces (intento original + 2 reintentos)
        assert call_count[0] >= 3
