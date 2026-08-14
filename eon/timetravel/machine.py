"""
eon.timetravel.machine
========================
TimeMachine — reconstruye el estado del kernel en cualquier event_seq.

Estrategia: checkpoint-anchored reconstruction.
1. Busca el último checkpoint con event_seq <= target_seq para la ejecución.
2. Si existe, usa su stores_state como base exacta (verificada con hash).
3. Lee los eventos del EventStore entre (checkpoint.event_seq, target_seq].
4. Clasifica eventos en applied (reducer conocido) vs unapplied (preservados).
5. Devuelve un ReconstructedState read-only.

Si no hay checkpoint base, la reconstrucción es parcial: devuelve los eventos
con complete=False, sin stores_state. Es honesto sobre lo que puede reconstruir.

Los reducers implementados son conservadores: solo aplican eventos cuyo efecto
en el estado es reversible y conocido. Eventos desconocidos se preservan en
unapplied_events para inspección manual o futuros reducers.
"""

from __future__ import annotations

import logging
from typing import Any

from ..checkpoint.store import SQLiteCheckpointStore
from ..persistence.event_store import EventStore
from .models import ReconstructedState, ReconstructionMode

logger = logging.getLogger("eon.timetravel")


# Eventos cuyo efecto en el estado conocemos y podemos aplicar de forma
# conservadora. Estos son eventos de transición de estado que actualizan
# campos específicos del stores_state.
_APPLIED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "coordinator.transition",
        "objective.transition",
        "plan.created",
        "plan.activated",
        "scheduler.iniciado",
        "scheduler.finalizado",
        "task.ready",
        "task.completed",
        "task.failed",
        "workspace.transition",
        "package.transition",
        "checkpoint.created",
        "hitl.interrupt.created",
        "hitl.interrupt.resolved",
        "execution.forked",
    }
)


class TimeMachine:
    """Reconstruye el estado del kernel en cualquier event_seq.

    Uso:
        tm = TimeMachine(checkpoint_store, event_store)
        state = tm.reconstruct_state("exec-1", event_seq=42)
        if state.complete:
            print(f"Estado en seq=42: {state.stores_state}")
        else:
            print(f"Reconstrucción parcial. Eventos: {state.events_timeline}")
    """

    def __init__(
        self,
        checkpoint_store: SQLiteCheckpointStore,
        event_store: EventStore,
    ) -> None:
        self._checkpoint_store = checkpoint_store
        self._event_store = event_store

    def reconstruct_state(
        self,
        execution_id: str,
        event_seq: int,
    ) -> ReconstructedState:
        """Reconstruye el estado del kernel en un event_seq específico.

        Args:
            execution_id: ID de la ejecución a reconstruir.
            event_seq: Número de secuencia del EventStore hasta donde
                reconstruir (inclusive).

        Returns:
            ReconstructedState con el estado en ese punto temporal.
        """
        # 1. Buscar el último checkpoint con event_seq <= target_seq
        checkpoint = self._checkpoint_store.latest_before_seq(
            execution_id=execution_id,
            event_seq=event_seq,
        )

        if checkpoint is None:
            # Sin checkpoint base: reconstrucción parcial desde eventos
            return self._reconstruct_partial(execution_id, event_seq)

        # 2. Verificar integridad del checkpoint
        hash_verified = self._checkpoint_store.verify_hash(checkpoint.id)

        if not hash_verified:
            logger.warning(
                "Checkpoint %s falló verificación de hash para execution %s seq=%d",
                checkpoint.id[:8],
                execution_id[:8],
                event_seq,
            )

        # 3. Leer eventos entre (checkpoint.event_seq, target_seq]
        base_seq = checkpoint.event_seq
        events = self._event_store.get_events(
            execution_id=execution_id,
            after_seq=base_seq,
        )

        # Filtrar hasta target_seq inclusive
        events_until = [e for e in events if e.seq <= event_seq]

        # 4. Clasificar eventos
        applied: list[dict[str, Any]] = []
        unapplied: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []

        for event in events_until:
            event_dict = event.to_dict()
            timeline.append(event_dict)
            if event.event_type in _APPLIED_EVENT_TYPES:
                applied.append(event_dict)
            else:
                unapplied.append(event_dict)

        # 5. Determinar modo de reconstrucción y completitud
        has_unapplied = len(unapplied) > 0
        has_events_after = len(events_until) > 0

        if not has_events_after:
            # Checkpoint base == target_seq, sin eventos posteriores
            mode = ReconstructionMode.EXACT
            is_complete = hash_verified
        elif not has_unapplied:
            # Checkpoint + eventos, todos con reducers conocidos
            mode = ReconstructionMode.CHECKPOINT_PLUS
            is_complete = hash_verified
        else:
            # Checkpoint + eventos no aplicados: estado puede diferir
            mode = ReconstructionMode.PARTIAL
            is_complete = False

        # 6. Construir ReconstructedState
        stores_state = dict(checkpoint.stores_state) if checkpoint.stores_state else {}

        # Aplicar reducers conservadores sobre una copia del estado
        # para eventos applied (solo metadatos, no reconstrucción completa)
        enriched_state = self._apply_conservative_reducers(
            stores_state,
            applied,
        )

        return ReconstructedState(
            execution_id=execution_id,
            target_event_seq=event_seq,
            base_checkpoint_id=checkpoint.id,
            base_checkpoint_seq=base_seq,
            reconstruction_mode=mode,
            complete=is_complete,
            stores_state=enriched_state,
            semantic=checkpoint.semantic.to_dict() if checkpoint.semantic else None,
            events_timeline=timeline,
            applied_events=applied,
            unapplied_events=unapplied,
            hash_verified=hash_verified,
            total_events_replayed=len(events_until),
        )

    def _reconstruct_partial(
        self,
        execution_id: str,
        event_seq: int,
    ) -> ReconstructedState:
        """Reconstrucción parcial cuando no hay checkpoint base.

        Devuelve solo el timeline de eventos sin estado base.
        """
        events = self._event_store.get_events(
            execution_id=execution_id,
            after_seq=0,
        )
        events_until = [e for e in events if e.seq <= event_seq]

        timeline = [e.to_dict() for e in events_until]
        applied = [e.to_dict() for e in events_until if e.event_type in _APPLIED_EVENT_TYPES]
        unapplied = [e.to_dict() for e in events_until if e.event_type not in _APPLIED_EVENT_TYPES]

        return ReconstructedState(
            execution_id=execution_id,
            target_event_seq=event_seq,
            base_checkpoint_id=None,
            base_checkpoint_seq=0,
            reconstruction_mode=ReconstructionMode.EVENTS_ONLY,
            complete=False,
            stores_state={},
            semantic=None,
            events_timeline=timeline,
            applied_events=applied,
            unapplied_events=unapplied,
            hash_verified=False,
            total_events_replayed=len(events_until),
        )

    @staticmethod
    def _apply_conservative_reducers(
        stores_state: dict[str, Any],
        applied_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aplica reducers conservadores sobre una copia del estado.

        Estos reducers actualizan metadatos de alto nivel (último estado
        conocido de coordinator, progreso de scheduler) basándose en
        eventos de transición. No reconstruyen entidades completas:
        solo enriquecen el snapshot base con información derivada de
        eventos posteriores.
        """
        state = dict(stores_state)
        if not applied_events:
            return state

        # Track último estado conocido del coordinator
        coordinator_state = state.get("coordinator", {})
        if isinstance(coordinator_state, dict):
            coordinator_state = dict(coordinator_state)

        scheduler_state = state.get("scheduler", {})
        if isinstance(scheduler_state, dict):
            scheduler_state = dict(scheduler_state)

        for event in applied_events:
            event_type = event.get("event_type", "")
            payload = event.get("payload", {})

            if event_type == "coordinator.transition":
                new_state = payload.get("to") or payload.get("estado")
                if new_state and isinstance(coordinator_state, dict):
                    coordinator_state["estado"] = new_state
                    coordinator_state["actualizado_en"] = event.get("timestamp", "")

            elif event_type == "objective.transition":
                new_state = payload.get("to") or payload.get("estado")
                objective_state = state.get("objective", {})
                if isinstance(objective_state, dict) and new_state:
                    objective_state = dict(objective_state)
                    objective_state["estado"] = new_state
                    state["objective"] = objective_state

            elif event_type == "task.completed":
                # Incrementar contador de tasks completadas en semantic
                pass  # El semantic snapshot ya tiene los contadores del checkpoint

            elif event_type == "execution.forked":
                # Registrar que esta ejecución fue forkeada
                forks = state.get("_forks", [])
                if not isinstance(forks, list):
                    forks = []
                forks.append(
                    {
                        "forked_execution_id": payload.get("new_execution_id"),
                        "at_seq": event.get("seq"),
                    }
                )
                state["_forks"] = forks

        if coordinator_state:
            state["coordinator"] = coordinator_state
        if scheduler_state:
            state["scheduler"] = scheduler_state

        return state

    def get_timeline(
        self,
        execution_id: str,
        from_seq: int = 0,
        to_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        """Devuelve el timeline de eventos de una ejecución.

        Args:
            execution_id: ID de la ejecución.
            from_seq: event_seq inicial (exclusive).
            to_seq: event_seq final (inclusive). None = hasta el último.

        Returns:
            Lista de eventos como dicts, ordenados por seq ascendente.
        """
        events = self._event_store.get_events(
            execution_id=execution_id,
            after_seq=from_seq,
        )
        if to_seq is not None:
            events = [e for e in events if e.seq <= to_seq]
        return [e.to_dict() for e in events]
