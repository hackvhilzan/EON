"""
eon.checkpoint.manager
========================
CheckpointManager — orquesta creación y recuperación de checkpoints.

Integración con KernelRuntime:
- crear_checkpoint(execution_id) captura estado de todos los stores
- recuperar_desde_checkpoint(checkpoint_id) reconstruye estado read-only
- auto_checkpoint(policy) crea checkpoints automáticamente

Políticas de auto-checkpoint:
- "manual": solo explícito (default)
- "milestone": en hitos (plan creado, task completada, ejecución finalizada)
- "every_task": después de cada task
"""

from __future__ import annotations

import logging
from typing import Any

from .models import Checkpoint, CheckpointKind
from .semantic import SemanticSnapshotBuilder
from .store import SQLiteCheckpointStore

logger = logging.getLogger("eon.checkpoint")


class CheckpointManager:
    """Gestiona checkpoints del Kernel.

    Uso:
        manager = CheckpointManager(checkpoint_store, event_store, stores...)
        cp = manager.crear_checkpoint("exec-1", reason="milestone")
        latest = manager.ultimo_checkpoint("exec-1")
        state = manager.recuperar_desde_checkpoint(cp.id)
    """

    def __init__(
        self,
        checkpoint_store: SQLiteCheckpointStore,
        event_store: Any | None = None,
        objective_store: Any | None = None,
        planner_store: Any | None = None,
        scheduler_store: Any | None = None,
        workspace_store: Any | None = None,
        package_store: Any | None = None,
        worker_store: Any | None = None,
        coordinator_store: Any | None = None,
    ) -> None:
        self._store = checkpoint_store
        self._event_store = event_store
        self._objective_store = objective_store
        self._planner_store = planner_store
        self._scheduler_store = scheduler_store
        self._workspace_store = workspace_store
        self._package_store = package_store
        self._worker_store = worker_store
        self._coordinator_store = coordinator_store
        self._snapshot_builder = SemanticSnapshotBuilder()
        self._auto_policy: str = "manual"

    def set_auto_policy(self, policy: str) -> None:
        """Configura la política de auto-checkpoint.

        Args:
            policy: "manual", "milestone", o "every_task"
        """
        if policy not in ("manual", "milestone", "every_task"):
            raise ValueError(f"Política desconocida: {policy}")
        self._auto_policy = policy

    @property
    def auto_policy(self) -> str:
        return self._auto_policy

    def crear_checkpoint(
        self,
        execution_id: str,
        reason: str = "",
        kind: CheckpointKind = CheckpointKind.MANUAL,
    ) -> Checkpoint:
        """Crea un checkpoint del estado actual.

        Captura:
        - Estado de los 7 stores relevante a la ejecución
        - SemanticSnapshot determinista
        - event_seq del EventStore (ancla al event log)
        - Hash de integridad
        """
        # Obtener event_seq actual
        event_seq = 0
        if self._event_store is not None:
            event_seq = self._event_store.get_last_seq()

        # Capturar estado técnico de los stores
        stores_state: dict[str, Any] = {}
        artifacts: dict[str, Any] = {}

        # CoordinatorExecution
        execution = None
        if self._coordinator_store is not None:
            execution = self._coordinator_store.get(execution_id)
            if execution is not None:
                stores_state["coordinator"] = execution.to_dict()

        # Objective
        objective = None
        if execution is not None and hasattr(execution, "objective_id") and self._objective_store is not None:
            objective = self._objective_store.get(execution.objective_id)
            if objective is not None:
                stores_state["objective"] = objective.to_dict()

        # Plan
        plan = None
        if execution is not None and hasattr(execution, "plan_id") and self._planner_store is not None:
            plan = self._planner_store.get(execution.plan_id)
            if plan is not None:
                stores_state["plan"] = plan.to_dict()

        # SchedulerRun
        scheduler_run = None
        if execution is not None and hasattr(execution, "plan_id") and self._scheduler_store is not None:
            try:
                scheduler_run = self._scheduler_store.obtener(execution.plan_id)
                stores_state["scheduler"] = scheduler_run.to_dict()
            except Exception:
                pass  # Puede no existir el plan en el scheduler

        # Workspace
        workspace = None
        if execution is not None and hasattr(execution, "workspace_id") and self._workspace_store is not None:
            workspace = self._workspace_store.get(execution.workspace_id)
            if workspace is not None:
                stores_state["workspace"] = workspace.to_dict()

        # Package
        package = None
        if execution is not None and hasattr(execution, "package_id") and self._package_store is not None:
            package = self._package_store.get(execution.package_id)
            if package is not None:
                stores_state["package"] = package.to_dict()
                if package.artifacts_path:
                    artifacts["path"] = package.artifacts_path

        # Workers (estado global, no por ejecución)
        if self._worker_store is not None:
            workers = self._worker_store.list()
            stores_state["workers"] = [w.to_dict() for w in workers]

        # Semantic Snapshot
        semantic = self._snapshot_builder.build(
            execution_id=execution_id,
            objective=objective,
            plan=plan,
            scheduler_run=scheduler_run,
            workspace=workspace,
            package=package,
            execution=execution,
        )

        # Crear checkpoint
        checkpoint = Checkpoint(
            execution_id=execution_id,
            event_seq=event_seq,
            kind=kind,
            stores_state=stores_state,
            artifacts=artifacts,
            semantic=semantic,
            reason=reason,
        )
        checkpoint.compute_hash()

        # Persistir
        self._store.save(checkpoint)

        # Evento ligero en EventStore
        if self._event_store is not None:
            self._event_store.append(
                execution_id=execution_id,
                event_type="checkpoint.created",
                payload={
                    "checkpoint_id": checkpoint.id,
                    "event_seq": checkpoint.event_seq,
                    "kind": checkpoint.kind.value,
                    "reason": reason,
                },
            )

        logger.info(
            "Checkpoint %s creado para execution %s (seq=%d, kind=%s)",
            checkpoint.id[:8],
            execution_id[:8],
            event_seq,
            kind.value,
        )
        return checkpoint

    def obtener_checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        """Obtiene un checkpoint por ID."""
        return self._store.get(checkpoint_id)

    def ultimo_checkpoint(self, execution_id: str) -> Checkpoint | None:
        """Obtiene el checkpoint más reciente de una ejecución."""
        return self._store.latest_for_execution(execution_id)

    def listar_checkpoints(self, execution_id: str) -> list[Checkpoint]:
        """Lista todos los checkpoints de una ejecución."""
        return self._store.list_for_execution(execution_id)

    def recuperar_desde_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        """Recupera el estado desde un checkpoint (read-only).

        Devuelve un dict con:
        - checkpoint: el checkpoint completo
        - stores_state: estado de los stores en ese momento
        - semantic: semantic snapshot
        - events_after: eventos del EventStore posteriores al checkpoint

        No hace rollback destructivo. Es read-only.
        """
        cp = self._store.get(checkpoint_id)
        if cp is None:
            return None

        # Eventos posteriores al checkpoint
        events_after: list[dict] = []
        if self._event_store is not None:
            events = self._event_store.get_events(
                execution_id=cp.execution_id,
                after_seq=cp.event_seq,
            )
            events_after = [e.to_dict() for e in events]

        # Verificar integridad
        hash_ok = self._store.verify_hash(checkpoint_id)

        return {
            "checkpoint": cp.to_dict(),
            "stores_state": cp.stores_state,
            "semantic": cp.semantic.to_dict() if cp.semantic else None,
            "events_after": events_after,
            "hash_verified": hash_ok,
            "recoverable": hash_ok,
        }

    def maybe_auto_checkpoint(
        self,
        execution_id: str,
        trigger: str,
    ) -> Checkpoint | None:
        """Crea un checkpoint automático si la política lo permite.

        Args:
            execution_id: ID de la ejecución.
            trigger: "task_completed", "plan_created", "execution_finished"

        Returns:
            Checkpoint si se creó, None si la política no lo permite.
        """
        if self._auto_policy == "manual":
            return None

        if self._auto_policy == "every_task" and trigger == "task_completed":
            return self.crear_checkpoint(
                execution_id,
                reason=f"auto: {trigger}",
                kind=CheckpointKind.EVERY_TASK,
            )

        if self._auto_policy == "milestone" and trigger in (
            "plan_created",
            "execution_finished",
            "task_completed",
        ):
            return self.crear_checkpoint(
                execution_id,
                reason=f"auto: {trigger}",
                kind=CheckpointKind.MILESTONE,
            )

        return None
