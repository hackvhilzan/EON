from __future__ import annotations

from pathlib import Path

from eon.objectives.models import ObjectiveState
from eon.planner.task import Task
from eon.runtime import KernelRuntime


def test_kernel_runtime_creates_package(tmp_path: Path) -> None:
    tasks = [Task(capability_id="default")]
    runtime = KernelRuntime(root=tmp_path, tasks=tasks)
    result = runtime.run("Demo objective", "El objetivo debe completarse.")

    assert result.package_state == "ready"
    assert result.package_id

    package = runtime.package_manager.obtener(result.package_id)
    assert package.estado.value == "ready"

    artifacts_dir = tmp_path / "workspace" / package.workspace_id / "artifacts"
    assert artifacts_dir.exists()
    assert any(artifacts_dir.iterdir())


def test_kernel_runtime_creates_package_sin_tasks_manuales(tmp_path: Path) -> None:
    """Fase 2 (Task Generation) -- criterio de aceptación: se puede iniciar
    una ejecución de punta a punta dando solo descripción y criterio de
    éxito, sin proporcionar ninguna Task manualmente. La generación inicial
    pasa por `TaskGenerationPort` (`DeterministicTaskGenerator` por
    defecto en `KernelRuntime` cuando no se le da `tasks=`)."""
    runtime = KernelRuntime(root=tmp_path)
    result = runtime.run("Demo objective", "El objetivo debe completarse.")

    assert result.package_state == "ready"
    assert result.package_id

    package = runtime.package_manager.obtener(result.package_id)
    assert package.estado.value == "ready"


def test_kernel_runtime_reintenta_tras_rechazo_y_completa(tmp_path: Path) -> None:
    """Fase 5 (bugfix verificar->reintentar), Test B/F: un rechazo del
    Verifier se reintenta con éxito de punta a punta usando el
    ObjectiveManager REAL (máquina de estados real, sin Fakes). Antes del
    fix esto lanzaba IllegalTransitionError('en_progreso' -> 'en_progreso')
    porque el Coordinator llamaba a Objectives.reintentar() sin haber
    aplicado antes el VerificationResult rechazado."""
    intentos = {"n": 0}

    def ejecutor(capability_id: str, parametros: dict) -> bool:
        intentos["n"] += 1
        return intentos["n"] > 1  # falla el primer intento, tiene éxito los siguientes

    tasks = [Task(capability_id="default")]
    runtime = KernelRuntime(root=tmp_path, tasks=tasks, task_executor=ejecutor, max_reintentos=2)
    result = runtime.run("Demo objective", "El objetivo debe completarse.")

    assert result.package_state == "ready"
    assert result.package_id

    execution = runtime.coordinator.obtener(result.execution_id)
    objetivo = runtime._objective_manager.obtener(execution.objective_id)
    assert objetivo.estado == ObjectiveState.COMPLETADO

    # El historial del Objetivo real conservó el paso por "fallando"
    # (Objectives.verificar aplicado) antes de volver a "en_progreso"
    # (Objectives.reintentar) -- confirma que Coordinator nunca intentó
    # en_progreso -> en_progreso (Test E).
    transiciones = [(h["de"], h["a"]) for h in objetivo.historial]
    assert ("en_progreso", "verificando") in transiciones
    assert ("verificando", "fallando") in transiciones
    assert ("fallando", "en_progreso") in transiciones


def test_kernel_runtime_agota_reintentos_y_falla_sin_package(tmp_path: Path) -> None:
    """Fase 5, Test C/E: con max_reintentos=0 y evidencia siempre
    rechazada, el flujo real (ObjectiveManager + CoordinatorManager +
    ObjectivesPortAdapter) debe terminar en Workspace FAILED / Execution
    FAILED, sin Package -- traduciendo RetryLimitExceededError (señal de
    dominio de Objectives) a RetriesExhaustedError (contrato que
    CoordinatorManager espera), todo ello sin que el Coordinator importe
    eon.objectives (aislamiento verificado en coordinator/tests)."""
    tasks = [Task(capability_id="default")]
    runtime = KernelRuntime(
        root=tmp_path,
        tasks=tasks,
        task_executor=lambda *_: False,
        max_reintentos=0,
    )
    execution = runtime.coordinator.iniciar_ejecucion("Demo objective", "El objetivo debe completarse.")

    final = runtime.coordinator.obtener(execution.id)
    assert final.estado.value == "failed"
    assert final.package_id is None

    objetivo = runtime._objective_manager.obtener(final.objective_id)
    assert objetivo.estado == ObjectiveState.FALLIDO


def test_objectives_port_adapter_traduce_retry_limit_exceeded(tmp_path: Path) -> None:
    """Fase 5, Test H: `ObjectivesPortAdapter.reintentar()` (el único
    punto de integración permitido para esta traducción -- CoordinatorManager
    tiene prohibido importar `eon.objectives`) atrapa
    `RetryLimitExceededError` (señal de dominio de
    `ObjectiveManager.reintentar()`) y la relanza como `RetriesExhaustedError`
    (la señal que `eon.coordinator.ports.ObjectivesPort.reintentar` promete),
    sin perder la causa original."""
    from eon.coordinator.exceptions import RetriesExhaustedError
    from eon.event_bus import EventBus
    from eon.objectives import InMemoryObjectiveStore, ObjectiveManager
    from eon.runtime import ObjectivesPortAdapter

    store = InMemoryObjectiveStore()
    bus = EventBus()
    manager = ObjectiveManager(store, bus, max_reintentos=0)
    adapter = ObjectivesPortAdapter(manager)

    objetivo = manager.crear_objetivo("Demo", "Criterio")
    manager.planificar(objetivo.id)
    manager.iniciar(objetivo.id)
    manager.verificar(objetivo.id, resultado=(False, 0.1))  # -> fallando

    try:
        adapter.reintentar(objetivo.id)
    except RetriesExhaustedError as exc:
        assert exc.objective_id == objetivo.id
        assert exc.__cause__ is not None
        assert type(exc.__cause__).__name__ == "RetryLimitExceededError"
    else:
        raise AssertionError("ObjectivesPortAdapter.reintentar() debía lanzar RetriesExhaustedError")

    assert manager.obtener(objetivo.id).estado == ObjectiveState.FALLIDO


def test_kernel_runtime_reutiliza_worker_liberado_en_replanificacion(tmp_path: Path) -> None:
    """Fase 6 (Worker Lifecycle & Resource Management) -- criterio de
    aceptación: Task 1 -> Worker A -> COMPLETED -> Worker A liberado ->
    Task 2 (de la replanificación) -> Worker A -> COMPLETED, con un único
    Worker registrado por capability, sin registrar ninguno artificialmente.

    Antes del fix, `Dispatcher.despachar()` no liberaba su Worker hasta que
    `TaskExecutor.ejecutar()` retornaba -- pero `ejecutar()` ya había
    emitido `task_fallida` de forma síncrona, lo que en este runtime
    desencadena, en el mismo hilo de llamada, la replanificación completa
    (Coordinator -> Scheduler -> `_on_task_ready` -> `despachar()` de nuevo)
    *antes* de que el despacho original llegara a liberar su Worker --
    dejándolo BUSY para el segundo intento y lanzando
    `NoCompatibleWorkerError`."""
    intentos = {"n": 0}

    def ejecutor(capability_id: str, parametros: dict) -> bool:
        intentos["n"] += 1
        return intentos["n"] > 1  # falla el primer intento, tiene éxito el segundo

    tasks = [Task(capability_id="default")]
    runtime = KernelRuntime(root=tmp_path, tasks=tasks, task_executor=ejecutor, max_reintentos=2)

    # Exactamente un Worker para "default" -- ninguno adicional.
    workers_default = [w for w in runtime._worker_manager.listar() if w.declara("default")]
    assert len(workers_default) == 1
    worker_id = workers_default[0].id

    result = runtime.run("Demo objective", "El objetivo debe completarse.")

    assert result.package_state == "ready"
    assert intentos["n"] == 2  # ambos intentos pasaron por el mismo (único) Worker

    worker_final = runtime._worker_manager.obtener(worker_id)
    from eon.workers import WorkerState

    assert worker_final.estado == WorkerState.IDLE
    assert worker_final.task_actual is None


def test_kernel_runtime_worker_disponible_para_segunda_task_tras_completar(tmp_path: Path) -> None:
    """Fase 6, criterio de aceptación (caso directo, sin replanificación):
    dos Tasks independientes que comparten capability y solo tienen un
    Worker disponible deben poder ejecutarse ambas -- la segunda solo puede
    despacharse si la primera realmente liberó su Worker."""
    from eon.workers import WorkerState

    tasks = [
        Task(id="t1", capability_id="default", depende_de=()),
        Task(id="t2", capability_id="default", depende_de=("t1",)),
    ]
    runtime = KernelRuntime(root=tmp_path, tasks=tasks)

    workers_default = [w for w in runtime._worker_manager.listar() if w.declara("default")]
    assert len(workers_default) == 1
    worker_id = workers_default[0].id

    result = runtime.run("Demo objective", "El objetivo debe completarse.")

    assert result.package_state == "ready"
    worker_final = runtime._worker_manager.obtener(worker_id)
    assert worker_final.estado == WorkerState.IDLE


def test_run_execution_id_explicito_es_respetado(tmp_path: Path) -> None:
    """CONSOLE.md §6: un llamador (el backend de la Console) necesita
    generar el execution_id ANTES de invocar `.run()`, para devolverlo de
    inmediato sin esperar a que la ejecución termine. `.run()` debe
    respetar ese id tal cual, no generar uno nuevo."""
    tasks = [Task(capability_id="default")]
    runtime = KernelRuntime(root=tmp_path, tasks=tasks)
    id_pregenerado = "id-generado-por-la-console-antes-de-lanzar-el-subproceso"

    result = runtime.run("Demo objective", "El objetivo debe completarse.", execution_id=id_pregenerado)

    assert result.execution_id == id_pregenerado
    assert result.package_state == "ready"


def test_run_sin_execution_id_sigue_autogenerando_uno(tmp_path: Path) -> None:
    """Retrocompatibilidad explícita: ningún llamador existente (CLI,
    tests previos) pasa `execution_id`, y debe seguir comportándose
    exactamente igual que antes de CONSOLE.md §6 -- un UUID autogenerado
    por `CoordinatorManager.iniciar_ejecucion`, nunca `None` ni vacío."""
    tasks = [Task(capability_id="default")]
    runtime = KernelRuntime(root=tmp_path, tasks=tasks)

    result = runtime.run("Demo objective", "El objetivo debe completarse.")

    assert result.execution_id
    import uuid

    uuid.UUID(result.execution_id)  # no lanza -> es un UUID válido
