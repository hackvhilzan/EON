"""Test E2E del flujo completo pedido tras Fase 14 + LLMTool: Objective ->
Planner -> Scheduler -> Workers -> `tool.llm` (CapabilityExecutor real ->
ToolRegistry real -> LLMTool real) -> resultado -> Verifier real -> Package
READY.

Usa el Kernel real de punta a punta -- mismo patrón que
`eon/tests/test_runtime.py` -- sin ningún Fake de Coordinator/Objectives/
Planner/Scheduler/Workers/Workspace/Package. Solo el `LLM` subyacente se
sustituye por un doble sin red (los proveedores reales -- Claude/OpenAI/
Gemini -- ya se prueban por separado y sin red en `eon/llm/tests/`).
"""

from __future__ import annotations

from pathlib import Path

from eon.capabilities import CapabilityExecutor
from eon.llm.base import LLM
from eon.objectives.models import ObjectiveState
from eon.planner.task import Task
from eon.runtime import KernelRuntime
from eon.tools.llm_tool import LLMTool
from eon.tools.registry import ToolRegistry


def _registry_con_llm(llm: LLM) -> ToolRegistry:
    """`ToolRegistry.autodiscover()` instancia cada Tool sin argumentos
    (`obj()`), así que `LLMTool` siempre construiría su propio
    `get_provider_lazy()` real -- no hay forma de inyectar un LLM de
    prueba a través de `autodiscover()`. La vía soportada es registrar la
    Tool ya construida a mano, exactamente como aquí, y pasar ese
    `ToolRegistry` a `CapabilityExecutor(tool_registry=...)`. El `llm=`
    de `CapabilityExecutor` NO llega a `LLMTool` -- solo alimenta el
    atajo síncrono `CapabilityExecutor.generar()` (CAPABILITIES.md §2),
    un camino distinto y paralelo al de `tool.llm` vía `ToolRegistry`."""
    registry = ToolRegistry()
    registry.register(LLMTool(llm=llm))
    return registry


class _LLMDeEco(LLM):
    name = "eco"

    def __init__(self):
        self.prompts_recibidos: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts_recibidos.append(prompt)
        return f"eco:{prompt}"


class _LLMQueFalla(LLM):
    name = "falla"

    def generate(self, prompt: str) -> str:
        raise RuntimeError("proveedor caído")


def test_objective_a_llmtool_a_package_ready_end_to_end(tmp_path: Path) -> None:
    """Camino feliz: la Task 'tool.llm' se resuelve por el CAPABILITY_MAP
    por defecto (sin capability_map a medida), pasa por el puente
    síncrono->asíncrono real de CapabilityExecutor, invoca de verdad
    ToolRegistry.execute('llm', ...) -> LLMTool.execute() -> LLM.generate(),
    y el Objetivo llega a completado / Package a READY."""
    llm_falso = _LLMDeEco()

    with CapabilityExecutor(tool_registry=_registry_con_llm(llm_falso)) as executor:
        tasks = [Task(capability_id="tool.llm", parametros={"prompt": "resume el estado del proyecto"})]
        runtime = KernelRuntime(root=tmp_path, tasks=tasks, task_executor=executor)

        result = runtime.run("Generar resumen vía LLM", "El resumen debe generarse correctamente.")

        assert result.package_state == "ready"
        assert result.package_id

        # Prueba de que el LLM se invocó a través del camino COMPLETO del
        # Kernel (no llamado directo): Task -> Dispatcher -> TaskExecutor ->
        # CapabilityExecutor -> ToolRegistry -> LLMTool -> LLM.generate.
        assert llm_falso.prompts_recibidos == ["resume el estado del proyecto"]
        assert executor.ultimo_resultado.ok is True
        assert executor.ultimo_resultado.data == "eco:resume el estado del proyecto"

        execution = runtime.coordinator.obtener(result.execution_id)
        objetivo = runtime._objective_manager.obtener(execution.objective_id)
        assert objetivo.estado == ObjectiveState.COMPLETADO

        package = runtime.package_manager.obtener(result.package_id)
        assert package.estado.value == "ready"

        artifacts_dir = tmp_path / "workspace" / package.workspace_id / "artifacts"
        assert artifacts_dir.exists()
        assert any(artifacts_dir.iterdir())


def test_objective_a_llmtool_fallo_del_llm_no_produce_package(tmp_path: Path) -> None:
    """Contraprueba: si el LLM subyacente lanza, CapabilityExecutor lo
    traduce a `False` sin dejar escapar la excepción (CAPABILITIES.md §2),
    la Task falla, y sin reintentos (`max_reintentos=0`) el Objetivo termina
    FALLIDO y la ejecución no produce Package -- mismo patrón que
    `test_kernel_runtime_agota_reintentos_y_falla_sin_package` en
    `test_runtime.py`, ahora con la capability real `tool.llm` en vez de
    un `task_executor` fake."""
    with CapabilityExecutor(tool_registry=_registry_con_llm(_LLMQueFalla())) as executor:
        tasks = [Task(capability_id="tool.llm", parametros={"prompt": "hola"})]
        runtime = KernelRuntime(root=tmp_path, tasks=tasks, task_executor=executor, max_reintentos=0)

        execution = runtime.coordinator.iniciar_ejecucion(
            "Generar resumen vía LLM", "El resumen debe generarse correctamente."
        )

        final = runtime.coordinator.obtener(execution.id)
        assert final.estado.value == "failed"
        assert final.package_id is None

        objetivo = runtime._objective_manager.obtener(final.objective_id)
        assert objetivo.estado == ObjectiveState.FALLIDO
        assert executor.ultimo_resultado.ok is False
        assert "proveedor caído" in executor.ultimo_resultado.error


def test_objective_a_llmtool_capability_id_no_mapeado_falla_sin_excepcion(tmp_path: Path) -> None:
    """Aislamiento de un capability_map a medida que excluye 'tool.llm' a
    propósito (mismo mecanismo con el que hoy se excluyen tool.terminal/
    tool.python, CAPABILITIES.md §4): el Kernel debe fallar la Task de forma
    controlada, nunca lanzar una excepción hacia el Dispatcher."""
    from eon.capabilities.capability_map import CAPABILITY_MAP

    mapa_sin_llm = {k: v for k, v in CAPABILITY_MAP.items() if k != "tool.llm"}

    with CapabilityExecutor(capability_map=mapa_sin_llm) as executor:
        tasks = [Task(capability_id="tool.llm", parametros={"prompt": "hola"})]
        runtime = KernelRuntime(root=tmp_path, tasks=tasks, task_executor=executor, max_reintentos=0)

        execution = runtime.coordinator.iniciar_ejecucion(
            "Generar resumen vía LLM", "El resumen debe generarse correctamente."
        )

        final = runtime.coordinator.obtener(execution.id)
        assert final.estado.value == "failed"
        assert executor.ultimo_resultado.ok is False
