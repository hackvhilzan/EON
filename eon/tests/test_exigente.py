"""
Prueba exigente para EON Kernel — Fase 4.

Escenario: Un pipeline de 3 tasks con dependencias, gobernanza activa,
un fallo controlado y verificación de auditoría.

Pipeline:
    Task A (default, allow) → Task B (default, allow, depende de A)
    → Task C (tool.terminal, DENY por política)

Lo que se verifica:
1. El preflight del GovernedTaskGenerator rechaza Task C antes del Plan
2. Si modificamos el LLM para que solo proponga A y B, la ejecución completa
   funciona con gobernanza
3. Una task que falla en primera instancia dispara el retry del Coordinator
4. El AuditLog registra todas las decisiones en orden
5. La cadena de hashes del AuditLog es verificable
6. El package llega a estado "ready"
7. Los artefactos del Workspace contienen los resultados
8. El histórico del Coordinator tiene la ejecución registrada

Segunda verificación independiente: se comprueba todo lo anterior
inspeccionando directamente los stores, el AuditLog y el EventBus,
sin usar las APIs de alto nivel del runtime.
"""

from __future__ import annotations

import json
import tempfile

from eon.governance import (
    CapabilityPolicy,
    PolicyDecision,
    PolicyEngine,
    SandboxProfile,
)
from eon.governance import events as governance_events
from eon.llm.base import LLM
from eon.runtime import KernelRuntime
from eon.task_generation import (
    GovernedTaskGenerator,
    GovernedTaskRejectionError,
    LLMTaskGenerator,
    TaskSpecValidator,
)


class FakeLLM(LLM):
    name = "fake"

    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, prompt: str) -> str:
        return self._response


class FakeObjective:
    def __init__(self, descripcion="Procesar datos", criterio="Resultado exitoso", id="obj-1"):
        self.descripcion = descripcion
        self.criterio_de_exito = criterio
        self.id = id
        self.estado = "pendiente"


def main():
    results = {}

    # ─── 1. Preflight rechaza capability denegada ──────────────

    llm_with_terminal = FakeLLM(
        json.dumps(
            [
                {"capability_id": "default", "id": "t1"},
                {"capability_id": "tool.terminal", "id": "t2", "depende_de": ["t1"]},
            ]
        )
    )

    engine = PolicyEngine(
        policies=[
            CapabilityPolicy(
                policy_id="allow-default",
                capabilities={"default"},
                decision=PolicyDecision.ALLOW,
                sandbox_profile=SandboxProfile.workspace_only(),
            ),
            CapabilityPolicy(
                policy_id="deny-terminal",
                capabilities={"tool.terminal"},
                decision=PolicyDecision.DENY,
                reason="terminal no permitido por política",
            ),
        ]
    )

    gen_rejected = GovernedTaskGenerator(
        inner=LLMTaskGenerator(llm_with_terminal, capabilities_validas=["default", "tool.terminal"]),
        validator=TaskSpecValidator(capabilities_validas=["default", "tool.terminal"]),
        policy_engine=engine,
    )

    try:
        gen_rejected.generar(FakeObjective())
        results["preflight_rejected"] = False
    except GovernedTaskRejectionError as e:
        results["preflight_rejected"] = True
        results["preflight_rejected_count"] = len(e.rejected)
        results["preflight_rejected_capability"] = e.rejected[0]["capability_id"]
        results["preflight_rejected_policy"] = e.rejected[0]["policy_id"]

    # ─── 2. Pipeline válido con dependencias y fallo controlado ──

    llm_valid = FakeLLM(
        json.dumps(
            [
                {"capability_id": "default", "id": "t1"},
                {"capability_id": "default", "id": "t2", "depende_de": ["t1"]},
            ]
        )
    )

    gen_valid = GovernedTaskGenerator(
        inner=LLMTaskGenerator(llm_valid, capabilities_validas=["default"]),
        validator=TaskSpecValidator(capabilities_validas=["default"]),
        policy_engine=engine,
    )

    # Executor que falla la primera vez para t1, pasa la segunda (retry)
    call_count: dict[str, int] = {}

    def failing_then_success(capability_id: str, params: dict) -> bool:
        task_id = params.get("task_id", "unknown")
        call_count[task_id] = call_count.get(task_id, 0) + 1
        # t1 falla la primera vez, pasa la segunda
        if task_id == "t1" and call_count[task_id] == 1:
            return False
        return True

    root_dir = tempfile.mkdtemp()

    runtime = KernelRuntime(
        root=root_dir,
        task_executor=failing_then_success,
        task_generation=gen_valid,
        worker_capabilities=["default"],
        policy_engine=engine,
    )

    # Capturar eventos del EventBus
    emitted_events: list[str] = []
    for evt in [
        governance_events.POLICY_EVALUATED,
        governance_events.POLICY_DENIED,
        governance_events.SANDBOX_APPLIED,
    ]:

        def make_handler(e):
            return lambda **kw: emitted_events.append(e)

        runtime._event_bus.subscribe(evt, make_handler(evt))

    # Ejecutar
    try:
        result = runtime.run(
            descripcion="Pipeline de procesamiento con dependencias",
            criterio_de_exito="Todas las tasks completadas",
        )
        results["execution_succeeded"] = True
        results["execution_id"] = result.execution_id
        results["package_id"] = result.package_id
        results["package_state"] = result.package_state
    except Exception as e:
        results["execution_succeeded"] = False
        results["execution_error"] = str(e)

    # ─── 3. Verificación del AuditLog ───────────────────────────

    audit = runtime.audit_log
    results["audit_total_entries"] = len(audit)
    results["audit_integrity_valid"] = audit.verificar_integridad()

    if results.get("execution_id"):
        replay = audit.replay(results["execution_id"])
        results["audit_replay_count"] = len(replay)
        results["audit_replay_decisions"] = [e.decision for e in replay]
        results["audit_replay_events"] = [e.event_type for e in replay]

    # ─── 4. Verificación del EventBus ──────────────────────────

    results["events_emitted"] = emitted_events
    results["policy_evaluated_emitted"] = governance_events.POLICY_EVALUATED in emitted_events
    results["sandbox_applied_emitted"] = governance_events.SANDBOX_APPLIED in emitted_events

    # ─── 5. Verificación del Workspace ──────────────────────────

    try:
        ws_list = runtime._workspace_store.list()
        results["workspace_count"] = len(ws_list)
        if ws_list:
            ws_id = ws_list[0].id
            artefactos = runtime._workspace_manager.artefactos(ws_id)
            results["workspace_artifacts"] = artefactos.listar() if hasattr(artefactos, "listar") else []
    except Exception as e:
        results["workspace_error"] = str(e)

    # ─── 6. Verificación del Package ───────────────────────────

    try:
        if results.get("package_id"):
            pkg = runtime._package_manager.obtener(results["package_id"])
            results["package_exists"] = pkg is not None
            results["package_estado"] = pkg.estado.value if pkg else None
    except Exception as e:
        results["package_error"] = str(e)

    # ─── 7. Verificación del Coordinator ───────────────────────

    try:
        if results.get("execution_id"):
            exec_obj = runtime.coordinator.obtener(results["execution_id"])
            results["coordinator_execution_found"] = exec_obj is not None
            results["coordinator_execution_state"] = str(exec_obj.estado) if exec_obj else None
    except Exception as e:
        results["coordinator_error"] = str(e)

    # ─── 8. Call counts del executor ───────────────────────────

    results["executor_call_counts"] = call_count

    # ─── Output ────────────────────────────────────────────────

    print(json.dumps(results, indent=2, default=str, ensure_ascii=False))
    return results


if __name__ == "__main__":
    main()
