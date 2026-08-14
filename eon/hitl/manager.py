"""
eon.hitl.manager
=================
HITLManager — orquesta interrupciones, aprobaciones y reanudación.
"""
from __future__ import annotations

import logging
from typing import Any

from .models import HITLInterrupt, HITLStatus
from .store import SQLiteHITLStore

logger = logging.getLogger("eon.hitl")


class HITLManager:
    """Gestiona el ciclo de vida de interrupciones HITL.

    Uso:
        manager = HITLManager(hitl_store, checkpoint_manager=cp_mgr)
        interrupt = manager.crear_interrupcion(
            execution_id="exec-1",
            task_id="task-5",
            tool_name="tool.terminal",
            reason="REQUIRES_APPROVAL",
            payload={"cmd": "rm -rf /"},
        )
        # Humano revisa...
        manager.aprobar(interrupt.id, decided_by="admin", reason="OK")
        context = manager.reanudar(interrupt.id)
    """

    def __init__(
        self,
        hitl_store: SQLiteHITLStore,
        checkpoint_manager: Any | None = None,
        event_store: Any | None = None,
    ) -> None:
        self._store = hitl_store
        self._checkpoint_manager = checkpoint_manager
        self._event_store = event_store

    def crear_interrupcion(
        self,
        execution_id: str,
        task_id: str = "",
        tool_name: str = "",
        reason: str = "",
        payload: dict | None = None,
    ) -> HITLInterrupt:
        """Crea una interrupción persistente.

        Si hay checkpoint_manager disponible, crea un checkpoint PRE_INTERRUPT
        y lo vincula a la interrupción.
        """
        interrupt = HITLInterrupt(
            execution_id=execution_id,
            task_id=task_id,
            tool_name=tool_name,
            reason=reason,
            payload=payload or {},
        )

        # Crear checkpoint PRE_INTERRUPT si hay checkpointing
        if self._checkpoint_manager is not None:
            try:
                from eon.checkpoint.models import CheckpointKind

                cp = self._checkpoint_manager.crear_checkpoint(
                    execution_id,
                    reason=f"PRE_INTERRUPT: {reason}",
                    kind=CheckpointKind.PRE_INTERRUPT,
                )
                interrupt.checkpoint_id = cp.id
            except Exception as exc:
                logger.warning("No se pudo crear checkpoint PRE_INTERRUPT: %s", exc)

        self._store.save(interrupt)

        # Evento en EventStore
        if self._event_store is not None:
            self._event_store.append(
                execution_id=execution_id,
                event_type="hitl.interrupt_created",
                payload={
                    "interrupt_id": interrupt.id,
                    "task_id": task_id,
                    "tool_name": tool_name,
                    "reason": reason,
                    "checkpoint_id": interrupt.checkpoint_id,
                },
            )

        logger.info(
            "Interrupción HITL %s creada para execution %s (task=%s, reason=%s)",
            interrupt.id[:8],
            execution_id[:8],
            task_id[:8] if task_id else "N/A",
            reason,
        )
        return interrupt

    def obtener_interrupcion(self, interrupt_id: str) -> HITLInterrupt | None:
        return self._store.get(interrupt_id)

    def listar_pendientes(self) -> list[HITLInterrupt]:
        return self._store.list_pending()

    def listar_por_ejecucion(self, execution_id: str) -> list[HITLInterrupt]:
        return self._store.list_for_execution(execution_id)

    def aprobar(
        self,
        interrupt_id: str,
        decided_by: str = "human",
        decision_reason: str = "",
    ) -> HITLInterrupt:
        """Aprueba una interrupción: PENDING → APPROVED."""
        interrupt = self._store.get(interrupt_id)
        if interrupt is None:
            raise ValueError(f"Interrupción no encontrada: {interrupt_id}")

        interrupt.transition_to(HITLStatus.APPROVED, decided_by, decision_reason)
        self._store.update(interrupt)

        if self._event_store is not None:
            self._event_store.append(
                execution_id=interrupt.execution_id,
                event_type="hitl.approved",
                payload={
                    "interrupt_id": interrupt_id,
                    "decided_by": decided_by,
                    "reason": decision_reason,
                },
            )

        logger.info("Interrupción %s aprobada por %s", interrupt_id[:8], decided_by)
        return interrupt

    def denegar(
        self,
        interrupt_id: str,
        decided_by: str = "human",
        decision_reason: str = "",
    ) -> HITLInterrupt:
        """Deniega una interrupción: PENDING → DENIED."""
        interrupt = self._store.get(interrupt_id)
        if interrupt is None:
            raise ValueError(f"Interrupción no encontrada: {interrupt_id}")

        interrupt.transition_to(HITLStatus.DENIED, decided_by, decision_reason)
        self._store.update(interrupt)

        if self._event_store is not None:
            self._event_store.append(
                execution_id=interrupt.execution_id,
                event_type="hitl.denied",
                payload={
                    "interrupt_id": interrupt_id,
                    "decided_by": decided_by,
                    "reason": decision_reason,
                },
            )

        logger.info("Interrupción %s denegada por %s", interrupt_id[:8], decided_by)
        return interrupt

    def reanudar(self, interrupt_id: str) -> dict | None:
        """Reanuda una ejecución desde una interrupción aprobada.

        APPROVED → RESUMED. Devuelve contexto recuperable:
        - interrupt: la interrupción
        - checkpoint: estado en el momento de la interrupción (si hay)
        - events_after: eventos posteriores al checkpoint
        """
        interrupt = self._store.get(interrupt_id)
        if interrupt is None:
            return None

        if interrupt.status != HITLStatus.APPROVED:
            raise ValueError(
                f"No se puede reanudar: estado actual es {interrupt.status.value}"
            )

        interrupt.transition_to(HITLStatus.RESUMED)
        self._store.update(interrupt)

        # Recuperar contexto del checkpoint si existe
        checkpoint_state: dict | None = None
        if interrupt.checkpoint_id and self._checkpoint_manager is not None:
            checkpoint_state = self._checkpoint_manager.recuperar_desde_checkpoint(
                interrupt.checkpoint_id
            )

        if self._event_store is not None:
            self._event_store.append(
                execution_id=interrupt.execution_id,
                event_type="hitl.resumed",
                payload={"interrupt_id": interrupt_id},
            )

        logger.info("Interrupción %s reanudada", interrupt_id[:8])
        return {
            "interrupt": interrupt.to_dict(),
            "checkpoint": checkpoint_state,
            "resumable": True,
        }

    def expirar(self, interrupt_id: str) -> HITLInterrupt | None:
        """Expira una interrupción pendiente por timeout."""
        interrupt = self._store.get(interrupt_id)
        if interrupt is None:
            return None

        interrupt.transition_to(HITLStatus.EXPIRED)
        self._store.update(interrupt)

        if self._event_store is not None:
            self._event_store.append(
                execution_id=interrupt.execution_id,
                event_type="hitl.expired",
                payload={"interrupt_id": interrupt_id},
            )

        logger.info("Interrupción %s expirada", interrupt_id[:8])
        return interrupt
