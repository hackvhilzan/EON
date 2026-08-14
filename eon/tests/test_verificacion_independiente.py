"""
Verificación independiente de la prueba exigente.

Inspecciona directamente los stores, archivos y estructuras
internas — sin usar las APIs de alto nivel del KernelRuntime.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from eon.governance import (
    CapabilityPolicy,
    PolicyDecision,
    PolicyEngine,
    SandboxProfile,
)
from eon.governance.audit_log import _calcular_hash
from eon.llm.base import LLM
from eon.runtime import KernelRuntime
from eon.task_generation import (
    GovernedTaskGenerator,
    LLMTaskGenerator,
    TaskSpecValidator,
)


class FakeLLM(LLM):
    name = "fake"

    def __init__(self, response: str):
        self._response = response

    def generate(self, prompt: str) -> str:
        return self._response


class FakeObjective:
    def __init__(self):
        self.descripcion = "Pipeline de procesamiento"
        self.criterio_de_exito = "Todas las tasks completadas"
        self.id = "obj-verify-1"
        self.estado = "pendiente"


def main():
    # ─── Reproducir el mismo escenario ──────────────────────────

    llm_valid = FakeLLM(
        json.dumps(
            [
                {"capability_id": "default", "id": "t1"},
                {"capability_id": "default", "id": "t2", "depende_de": ["t1"]},
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
                reason="terminal no permitido",
            ),
        ]
    )

    gen = GovernedTaskGenerator(
        inner=LLMTaskGenerator(llm_valid, capabilities_validas=["default"]),
        validator=TaskSpecValidator(capabilities_validas=["default"]),
        policy_engine=engine,
    )

    root_dir = tempfile.mkdtemp()

    runtime = KernelRuntime(
        root=root_dir,
        task_executor=lambda cap, params: True,
        task_generation=gen,
        worker_capabilities=["default"],
        policy_engine=engine,
    )

    result = runtime.run(
        descripcion="Pipeline de procesamiento",
        criterio_de_exito="Todas las tasks completadas",
    )

    verification = {}

    # ─── 1. Verificar AuditLog: cadena de hashes a mano ────────

    audit = runtime.audit_log
    entries = audit.entries

    # Verificar manualmente cada hash
    prev_hash = "genesis"
    hash_chain_valid = True
    for i, entry in enumerate(entries):
        if entry.previous_hash != prev_hash:
            hash_chain_valid = False
            verification[f"hash_break_at_{i}"] = f"expected {prev_hash}, got {entry.previous_hash}"
            break
        # Recalcular el hash
        entry_dict = asdict(entry)
        recalculated = _calcular_hash(entry_dict)
        if entry.entry_hash != recalculated:
            hash_chain_valid = False
            verification[f"hash_mismatch_at_{i}"] = (
                f"stored {entry.entry_hash[:16]}... vs recalculated {recalculated[:16]}..."
            )
            break
        prev_hash = entry.entry_hash

    verification["manual_hash_chain_valid"] = hash_chain_valid
    verification["manual_entry_count"] = len(entries)
    verification["manual_entry_decisions"] = [e.decision for e in entries]
    verification["manual_entry_policy_ids"] = [e.policy_id for e in entries]
    verification["manual_entry_task_ids"] = [e.task_id for e in entries]
    verification["manual_entry_execution_ids"] = [e.execution_id for e in entries]

    # Todas las entradas deben pertenecer a la misma ejecución
    verification["all_entries_same_execution"] = len(set(e.execution_id for e in entries)) == 1
    verification["entries_execution_matches_result"] = (
        len(entries) > 0 and entries[0].execution_id == result.execution_id
    )

    # ─── 2. Verificar Workspace: buscar archivos en disco ───────

    workspace_root = Path(root_dir) / "workspace"
    verification["workspace_root_exists"] = workspace_root.exists()

    # Buscar todos los archivos .txt en el árbol del workspace
    if workspace_root.exists():
        txt_files = list(workspace_root.rglob("*.txt"))
        verification["workspace_txt_files_on_disk"] = [f.name for f in txt_files]
        verification["workspace_txt_count"] = len(txt_files)

        # Leer el contenido de cada archivo
        for f in txt_files:
            try:
                content = json.loads(f.read_text(encoding="utf-8"))
                verification[f"file_{f.name}_has_task_id"] = "task_id" in content
                verification[f"file_{f.name}_has_worker_id"] = "worker_id" in content
                verification[f"file_{f.name}_task_id"] = content.get("task_id")
                verification[f"file_{f.name}_worker_id"] = content.get("worker_id")
            except Exception as e:
                verification[f"file_{f.name}_error"] = str(e)

    # ─── 3. Verificar Package: buscar en disco ─────────────────

    package_root = Path(root_dir) / "package"
    verification["package_root_exists"] = package_root.exists()

    # Buscar el manifest del package
    if package_root.exists():
        manifest_files = list(package_root.rglob("*.json")) + list(package_root.rglob("*.yaml"))
        verification["package_manifests_on_disk"] = [f.name for f in manifest_files]

    # Verificar via store directo
    pkg = runtime._package_store.get(result.package_id)
    verification["package_store_finds_it"] = pkg is not None
    verification["package_store_state"] = pkg.estado.value if pkg else None
    if pkg:
        verification["package_has_workspace_id"] = pkg.workspace_id is not None

    # ─── 4. Verificar Coordinator: buscar ejecución ────────────

    coord_root = Path(root_dir) / "coordinator"
    verification["coordinator_root_exists"] = coord_root.exists()

    # Listar archivos en el coordinator
    if coord_root.exists():
        coord_files = list(coord_root.rglob("*"))
        verification["coordinator_files_on_disk"] = [str(f.relative_to(coord_root)) for f in coord_files if f.is_file()]

    # Verificar via store directo
    try:
        execution = runtime._coordinator_store.get(result.execution_id)
    except Exception:
        execution = None
    verification["coordinator_store_finds_execution"] = execution is not None
    if execution:
        verification["coordinator_execution_state"] = str(execution.estado)
        verification["coordinator_has_package_id"] = execution.package_id == result.package_id

    # ─── 5. Verificar Scheduler: estado de tasks ────────────────

    # Buscar el plan_id y el scheduler_run
    plans = runtime._planner_store.list()
    verification["planner_store_count"] = len(plans)

    if plans:
        plan = plans[0]
        verification["plan_id"] = plan.id
        verification["plan_task_count"] = len(plan.tasks)
        verification["plan_task_ids"] = [t.id for t in plan.tasks]
        verification["plan_task_capabilities"] = [t.capability_id for t in plan.tasks]
        verification["plan_task_dependencies"] = [{t.id: list(t.depende_de)} for t in plan.tasks if t.depende_de]

        # Verificar el scheduler run
        try:
            run = runtime._scheduler_store.obtener(plan.id)
            verification["scheduler_run_found"] = run is not None
            if run:
                verification["scheduler_run_state"] = (
                    run.estado.value if hasattr(run.estado, "value") else str(run.estado)
                )
                # Verificar estado de cada task
                task_states = {}
                for tid, record in run.tasks.items():
                    task_states[tid] = record.estado.value if hasattr(record.estado, "value") else str(record.estado)
                verification["scheduler_task_states"] = task_states
        except Exception as e:
            verification["scheduler_error"] = str(e)

    # ─── 6. Verificar Workers ───────────────────────────────────

    workers = runtime._worker_store.list()
    verification["worker_count"] = len(workers)
    verification["worker_names"] = [w.nombre for w in workers]
    verification["worker_capabilities"] = [list(w.capabilities) for w in workers]

    # ─── 7. Verificar EventBus: suscriptores registrados ───────

    verification["event_bus_has_subscribers"] = len(runtime._event_bus._subscribers) > 0
    verification["event_bus_subscribed_events"] = list(runtime._event_bus._subscribers.keys())

    # ─── 8. Verificar dependencias del Plan ────────────────────

    if plans:
        plan = plans[0]
        # t2 depende de t1
        t2 = next((t for t in plan.tasks if t.id == "t2"), None)
        verification["t2_exists"] = t2 is not None
        verification["t2_depends_on_t1"] = t2 is not None and "t1" in t2.depende_de
        # t1 no depende de nada
        t1 = next((t for t in plan.tasks if t.id == "t1"), None)
        verification["t1_exists"] = t1 is not None
        verification["t1_no_dependencies"] = t1 is not None and len(t1.depende_de) == 0

    # ─── Output ────────────────────────────────────────────────

    print(json.dumps(verification, indent=2, default=str, ensure_ascii=False))
    return verification


if __name__ == "__main__":
    main()
