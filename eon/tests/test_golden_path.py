"""
eon.tests.test_golden_path
============================
Test de integración "golden path" — verifica que el ciclo completo de EON
funciona de punta a punta: Objective → Planner → Scheduler → Workers →
Verifier → Workspace → Package → Resultado.

Este test NO usa mocks para el flujo del Kernel: usa los managers reales
de cada módulo. El único doble es el `task_executor` inyectado, que en
producción sería un `CapabilityExecutor` conectado a Tools/LLM reales.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eon.runtime import KernelRunResult, KernelRuntime


class TestGoldenPath:
    """Ciclo completo: objetivo → plan → scheduler → workers → verifier → package."""

    def test_ejecucion_minima_completada(self, tmp_path: Path) -> None:
        """El camino feliz: un objetivo simple con una task que siempre
        devuelve True, verificación aprobada, y package en estado ready."""
        runtime = KernelRuntime(
            root=tmp_path / "eon_runtime",
            task_executor=lambda capability_id, parametros: True,
        )

        result = runtime.run(
            descripcion="Listar archivos de un directorio",
            criterio_de_exito="El resultado contiene al menos un archivo",
        )

        assert result is not None
        assert isinstance(result, KernelRunResult)
        assert result.execution_id is not None
        assert result.package_id is not None
        assert result.package_state == "ready"

        # La ejecución debe estar en estado COMPLETED.
        execution = runtime.coordinator.obtener(result.execution_id)
        assert execution.estado.value == "completed"
        assert execution.objective_id is not None
        assert execution.plan_id is not None
        assert execution.workspace_id is not None
        assert execution.package_id == result.package_id

    def test_ejecucion_con_tasks_explicitas(self, tmp_path: Path) -> None:
        """El runtime acepta Tasks explícitas en lugar del generador
        determinista por defecto."""
        from eon.planner.task import Task

        tasks = [
            Task(
                capability_id="default",
                parametros={"mensaje": "hola"},
            ),
        ]

        runtime = KernelRuntime(
            root=tmp_path / "eon_runtime",
            tasks=tasks,
            task_executor=lambda capability_id, parametros: True,
        )

        result = runtime.run(
            descripcion="Ejecutar una task explícita",
            criterio_de_exito="La task se ejecuta correctamente",
        )

        assert result.package_state == "ready"
        execution = runtime.coordinator.obtener(result.execution_id)
        assert execution.estado.value == "completed"

    def test_ejecucion_con_task_que_falla(self, tmp_path: Path) -> None:
        """Cuando la task falla (devuelve False), el scheduler finaliza
        con tasks_totales > tasks_completadas, la confianza es < 1.0,
        y el verifier rechaza, disparando replanificación o fallo."""
        runtime = KernelRuntime(
            root=tmp_path / "eon_runtime",
            task_executor=lambda capability_id, parametros: False,
            max_reintentos=0,  # sin reintentos: falla inmediatamente
        )

        # Con 0 reintentos y tarea fallando, run() lanza RuntimeError
        # porque no se produce un package (la ejecución termina en FAILED).
        with pytest.raises(RuntimeError, match="did not produce a package"):
            runtime.run(
                descripcion="Task que siempre falla",
                criterio_de_exito="Debe completarse",
            )

        # La ejecución debe estar en estado FAILED.
        ejecuciones = runtime.coordinator.listar()
        assert len(ejecuciones) == 1
        assert ejecuciones[0].estado.value == "failed"

    def test_ejecucion_deja_artefacto_en_workspace(self, tmp_path: Path) -> None:
        """Al completar una task, se escribe un artefacto en el workspace."""
        runtime = KernelRuntime(
            root=tmp_path / "eon_runtime",
            task_executor=lambda capability_id, parametros: True,
        )

        result = runtime.run(
            descripcion="Verificar artefactos",
            criterio_de_exito="Artefacto escrito",
        )

        execution = runtime.coordinator.obtener(result.execution_id)
        workspace = runtime.workspace_manager.obtener(execution.workspace_id)
        # El workspace debe estar COMPLETED.
        assert workspace.estado.value == "completed"

        # Debe haber al menos un artefacto escrito.
        registry = runtime.workspace_manager.artefactos(execution.workspace_id)
        artefactos = registry.listar()
        assert len(artefactos) > 0

    def test_historial_de_ejecuciones(self, tmp_path: Path) -> None:
        """El coordinator mantiene un historial de ejecuciones."""
        runtime = KernelRuntime(
            root=tmp_path / "eon_runtime",
            task_executor=lambda capability_id, parametros: True,
        )

        runtime.run(descripcion="Ejecución 1", criterio_de_exito="ok")
        runtime.run(descripcion="Ejecución 2", criterio_de_exito="ok")

        ejecuciones = runtime.coordinator.listar()
        assert len(ejecuciones) == 2
        for e in ejecuciones:
            assert e.estado.value == "completed"


class TestPackage:
    """Verifica que el Package se construye correctamente."""

    def test_package_ready_con_artefactos(self, tmp_path: Path) -> None:
        runtime = KernelRuntime(
            root=tmp_path / "eon_runtime",
            task_executor=lambda capability_id, parametros: True,
        )
        result = runtime.run(descripcion="Test package", criterio_de_exito="ok")

        package = runtime.package_manager.obtener(result.package_id)
        assert package is not None
        assert package.estado.value == "ready"
        assert package.workspace_id is not None


class TestEventBus:
    """Verifica que el EventBus compartido funciona correctamente."""

    def test_subscribe_y_emit(self):
        from eon.event_bus import EventBus

        bus = EventBus()
        recibido = []

        bus.subscribe("test_event", lambda **kw: recibido.append(kw))
        bus.emit("test_event", valor=42)

        assert len(recibido) == 1
        assert recibido[0]["valor"] == 42

    def test_callback_que_falla_no_rompe_flow(self):
        from eon.event_bus import EventBus

        bus = EventBus()
        llamado = []

        def callback_bueno(**kw):
            llamado.append("bueno")

        def callback_malo(**kw):
            raise RuntimeError("boom")

        bus.subscribe("evento", callback_malo)
        bus.subscribe("evento", callback_bueno)

        # El callback malo no debe impedir que el bueno se ejecute.
        bus.emit("evento")
        assert "bueno" in llamado


class TestTaskGeneration:
    """Verifica el generador determinista de tasks."""

    def test_deterministic_generator(self):
        from eon.task_generation import DeterministicTaskGenerator

        class ObjFake:
            id = "obj-123"
            descripcion = "test"
            criterio_de_exito = "ok"

        gen = DeterministicTaskGenerator()
        specs = gen.generar(ObjFake())

        assert len(specs) == 1
        assert specs[0].capability_id == "default"
        assert specs[0].id is not None
        assert specs[0].parametros["objective_id"] == "obj-123"


class TestWorkers:
    """Verifica el módulo de Workers."""

    def test_worker_registrar_y_liberar(self):
        from eon.event_bus import EventBus
        from eon.workers import InMemoryWorkerStore, WorkerManager

        bus = EventBus()
        store = InMemoryWorkerStore()
        manager = WorkerManager(store, bus)

        worker = manager.registrar(["default"], nombre="worker-test")
        assert worker.estado.value == "idle"

        manager.reservar(worker.id)
        assert worker.estado.value == "busy"

        manager.liberar(worker.id)
        assert worker.estado.value == "idle"

    def test_dispatcher_ejecuta_task(self):
        from eon.event_bus import EventBus
        from eon.planner.task import Task
        from eon.workers import (
            Dispatcher,
            InMemoryWorkerStore,
            TaskExecutor,
            WorkerManager,
            WorkerRegistry,
        )

        bus = EventBus()
        store = InMemoryWorkerStore()
        manager = WorkerManager(store, bus)
        registry = WorkerRegistry(store)
        executor = TaskExecutor(bus, ejecutar=lambda cap, params: True)

        manager.registrar(["default"], nombre="worker-1")
        dispatcher = Dispatcher(registry, manager, executor)

        eventos = []
        bus.subscribe("task_completada", lambda **kw: eventos.append("completada"))
        bus.subscribe("task_fallida", lambda **kw: eventos.append("fallida"))

        task = Task(capability_id="default", parametros={})
        dispatcher.despachar(task)

        assert "completada" in eventos


class TestTools:
    """Verifica el módulo de Tools."""

    def test_filesystem_tool_read_write(self):
        import asyncio

        from eon.tools.filesystem_tool import FilesystemTool

        async def run():
            tool = FilesystemTool()
            # Write
            result = await tool.execute(
                action="write",
                path="/tmp/eon_test_filesystem.txt",
                content="hola mundo",
            )
            assert result.ok

            # Read
            result = await tool.execute(
                action="read",
                path="/tmp/eon_test_filesystem.txt",
            )
            assert result.ok
            assert result.data == "hola mundo"

        asyncio.run(run())

    def test_tool_registry(self):
        import asyncio

        from eon.tools.filesystem_tool import FilesystemTool
        from eon.tools.registry import ToolRegistry

        async def run():
            reg = ToolRegistry()
            reg.register(FilesystemTool())
            assert "filesystem" in reg.list()

            result = await reg.execute(
                "filesystem",
                action="write",
                path="/tmp/eon_test_registry.txt",
                content="test",
            )
            assert result.ok

        asyncio.run(run())
