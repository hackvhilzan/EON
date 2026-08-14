"""
eon.checkpoint.semantic
=========================
SemanticSnapshotBuilder — construye SemanticSnapshot de forma determinista.

Extrae información semántica de los modelos existentes:
- Objective: intención, criterio de éxito, confianza, valor
- Plan: tareas, dependencias, estado
- SchedulerRun: progreso, fallos, bloqueos
- Workspace/Package: artefactos
- CoordinatorExecution.historial: narrativa de "por qué estamos aquí"

No usa LLM ni inferencias. Todo es extracción directa.
Si falta información, guarda listas vacías o None.
"""

from __future__ import annotations

from typing import Any

from .models import SemanticSnapshot


class SemanticSnapshotBuilder:
    """Construye SemanticSnapshot determinista desde los stores de EON.

    Uso:
        builder = SemanticSnapshotBuilder()
        snapshot = builder.build(
            objective=obj,
            plan=plan,
            scheduler_run=run,
            workspace=ws,
            package=pkg,
            execution=exec,
            execution_id="exec-1",
        )
    """

    def build(
        self,
        execution_id: str,
        objective: Any | None = None,
        plan: Any | None = None,
        scheduler_run: Any | None = None,
        workspace: Any | None = None,
        package: Any | None = None,
        execution: Any | None = None,
        cost_so_far: float = 0.0,
    ) -> SemanticSnapshot:
        """Construye un SemanticSnapshot desde los modelos de dominio."""
        snap = SemanticSnapshot()

        # Objective → intención, criterio, confianza, valor
        if objective is not None:
            snap.intent = getattr(objective, "descripcion", "")
            snap.success_criteria = getattr(objective, "criterio_de_exito", "")
            snap.objective_state = (
                getattr(objective.estado, "value", str(objective.estado)) if hasattr(objective, "estado") else ""
            )
            snap.confidence = getattr(objective, "confianza_minima", 0.0)
            snap.cost_so_far = cost_so_far

        # Plan → tareas, dependencias, estado
        if plan is not None:
            snap.plan_state = getattr(plan.estado, "value", str(plan.estado)) if hasattr(plan, "estado") else ""
            tasks = getattr(plan, "tasks", [])
            snap.tasks_total = len(tasks)
            # Extraer restricciones de las dependencias
            for task in tasks:
                deps = getattr(task, "depende_de", ())
                if deps:
                    snap.constraints.append(f"Task {task.id} depende de {list(deps)}")

        # SchedulerRun → progreso, fallos, bloqueos
        if scheduler_run is not None:
            snap.scheduler_progress = (
                getattr(scheduler_run.estado, "value", str(scheduler_run.estado))
                if hasattr(scheduler_run, "estado")
                else ""
            )
            tasks_dict = getattr(scheduler_run, "tasks", {})
            if isinstance(tasks_dict, dict):
                # Si no se setió tasks_total desde el plan, usar el scheduler
                if snap.tasks_total == 0:
                    snap.tasks_total = len(tasks_dict)
                completed = sum(
                    1
                    for r in tasks_dict.values()
                    if getattr(r.estado, "value", str(r.estado)) == "completed"
                    if hasattr(r, "estado")
                )
                failed = sum(
                    1
                    for r in tasks_dict.values()
                    if getattr(r.estado, "value", str(r.estado)) == "failed"
                    if hasattr(r, "estado")
                )
                snap.tasks_completed = completed
                snap.tasks_failed = failed
                # Riesgos: tasks bloqueadas
                for tid, record in tasks_dict.items():
                    estado = getattr(record.estado, "value", str(record.estado)) if hasattr(record, "estado") else ""
                    if estado == "blocked":
                        snap.risks.append(f"Task {tid} está bloqueada")
                    elif estado == "failed":
                        snap.risks.append(f"Task {tid} ha fallado")

        # Workspace → artefactos
        if workspace is not None:
            ws_estado = (
                getattr(workspace.estado, "value", str(workspace.estado)) if hasattr(workspace, "estado") else ""
            )
            snap.constraints.append(f"Workspace estado: {ws_estado}")

        # Package → artefactos y evidencia
        if package is not None:
            artifacts_path = getattr(package, "artifacts_path", "")
            if artifacts_path:
                snap.artifacts.append(artifacts_path)
            pkg_metadata = getattr(package, "metadata", {})
            if isinstance(pkg_metadata, dict) and pkg_metadata:
                snap.evidence["package"] = dict(pkg_metadata)

        # CoordinatorExecution → narrativa, decisión pendiente
        if execution is not None:
            historial = getattr(execution, "historial", [])
            if isinstance(historial, list) and historial:
                # Últimas 3 entradas como narrativa
                recent = historial[-3:]
                narratives = []
                for entry in recent:
                    if isinstance(entry, dict):
                        evento = entry.get("evento", "")
                        motivo = entry.get("motivo", "")
                        if evento:
                            narratives.append(f"{evento}" + (f": {motivo}" if motivo else ""))
                snap.why_here = " → ".join(narratives) if narratives else ""

            exec_estado = (
                getattr(execution.estado, "value", str(execution.estado)) if hasattr(execution, "estado") else ""
            )
            if exec_estado in ("paused", "awaiting_approval", "interrupted"):
                snap.pending_decision = f"Ejecución en estado: {exec_estado}"

        # Asunciones: si hay confianza baja, es una asunción
        if snap.confidence < 0.7 and snap.intent:
            snap.assumptions.append(f"Confianza baja ({snap.confidence:.2f}): resultado no garantizado")

        return snap
