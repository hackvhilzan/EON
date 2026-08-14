"""
eon.forking.manager
=====================
ForkManager — orquesta la creación de forks desde checkpoints.

Estrategia: deep-copy del estado del checkpoint en nuevos IDs.
1. Valida que el checkpoint existe y su hash verifica.
2. Crea una nueva CoordinatorExecution con nuevo ID.
3. Copia el estado de los stores del checkpoint, remapeando IDs.
4. Preserva metadatos del parent en un registro ExecutionFork durable.
5. Emite un evento execution.forked en el EventStore.
6. La ejecución original no se muta.

comparar_forks() hace un diff read-only de estados finales de dos forks.
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from ..checkpoint.models import Checkpoint, CheckpointKind
from ..checkpoint.store import SQLiteCheckpointStore
from ..persistence.event_store import EventStore
from .models import ExecutionFork, ForkStatus
from .store import SQLiteForkStore

logger = logging.getLogger("eon.forking")


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class ForkManager:
    """Gestiona forks de ejecución desde checkpoints.

    Uso:
        manager = ForkManager(fork_store, checkpoint_store, event_store)
        fork = manager.fork_from_checkpoint("cp-123", new_objective="...")
        # fork.new_execution_id es la nueva ejecución
        # La original no se ha mutado

        # Comparar dos forks
        diff = manager.comparar_forks("fork-1", "fork-2")
    """

    def __init__(
        self,
        fork_store: SQLiteForkStore,
        checkpoint_store: SQLiteCheckpointStore,
        event_store: EventStore | None = None,
    ) -> None:
        self._fork_store = fork_store
        self._checkpoint_store = checkpoint_store
        self._event_store = event_store

    def fork_from_checkpoint(
        self,
        checkpoint_id: str,
        new_objective: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionFork | None:
        """Crea un fork desde un checkpoint.

        Args:
            checkpoint_id: ID del checkpoint desde el que bifurcar.
            new_objective: Objetivo opcional modificado para el fork.
            metadata: Metadatos adicionales (razón, notas, etc.).

        Returns:
            ExecutionFork con los IDs de la nueva ejecución,
            o None si el checkpoint no existe o su hash no verifica.
        """
        # 1. Validar checkpoint
        cp = self._checkpoint_store.get(checkpoint_id)
        if cp is None:
            logger.error("Checkpoint %s no encontrado para fork", checkpoint_id[:8])
            return None

        # 2. Verificar integridad
        hash_ok = self._checkpoint_store.verify_hash(checkpoint_id)
        if not hash_ok:
            logger.error(
                "Checkpoint %s falló verificación de hash — fork cancelado",
                checkpoint_id[:8],
            )
            return None

        # 3. Generar nuevos IDs
        new_execution_id = str(uuid.uuid4())

        # 4. Deep-copy del estado del checkpoint en nuevos IDs
        #    Crear un checkpoint inicial para la nueva ejecución
        #    con el mismo estado pero remapeado al nuevo execution_id.
        new_checkpoint = Checkpoint(
            id=str(uuid.uuid4()),
            execution_id=new_execution_id,
            event_seq=0,  # nueva ejecución empieza en seq 0
            kind=CheckpointKind.RECOVERY,
            stores_state=copy.deepcopy(cp.stores_state),
            artifacts=copy.deepcopy(cp.artifacts),
            semantic=copy.deepcopy(cp.semantic),
            cost_so_far=cp.cost_so_far,
            reason=f"Fork from checkpoint {checkpoint_id[:8]} (execution {cp.execution_id[:8]})",
        )
        new_checkpoint.compute_hash()
        self._checkpoint_store.save(new_checkpoint)

        # 5. Crear registro de fork
        fork = ExecutionFork(
            parent_execution_id=cp.execution_id,
            parent_checkpoint_id=checkpoint_id,
            forked_at_seq=cp.event_seq,
            new_execution_id=new_execution_id,
            new_objective=new_objective,
            metadata=metadata or {},
        )

        # 6. Persistir fork
        self._fork_store.save(fork)

        # 7. Emitir eventos (en ambas ejecuciones para rastreabilidad)
        if self._event_store is not None:
            fork_payload = {
                "fork_id": fork.id,
                "parent_execution_id": cp.execution_id,
                "parent_checkpoint_id": checkpoint_id,
                "new_execution_id": new_execution_id,
                "forked_at_seq": cp.event_seq,
                "new_checkpoint_id": new_checkpoint.id,
                "new_objective": new_objective,
            }
            # Evento en la ejecución padre (rastreabilidad)
            self._event_store.append(
                execution_id=cp.execution_id,
                event_type="execution.forked",
                payload=fork_payload,
            )
            # Evento en la nueva ejecución (punto de partida)
            self._event_store.append(
                execution_id=new_execution_id,
                event_type="execution.forked",
                payload=fork_payload,
            )

        logger.info(
            "Fork %s creado: exec %s → exec %s (from checkpoint %s, seq=%d, new_cp=%s)",
            fork.id[:8],
            cp.execution_id[:8],
            new_execution_id[:8],
            checkpoint_id[:8],
            cp.event_seq,
            new_checkpoint.id[:8],
        )

        return fork

    def obtener_fork(self, fork_id: str) -> ExecutionFork | None:
        """Obtiene un fork por ID."""
        return self._fork_store.get(fork_id)

    def listar_forks(self, parent_execution_id: str) -> list[ExecutionFork]:
        """Lista todos los forks de una ejecución parent."""
        return self._fork_store.list_for_parent(parent_execution_id)

    def listar_todos_forks(self) -> list[ExecutionFork]:
        """Lista todos los forks."""
        return self._fork_store.list_all()

    def actualizar_estado_fork(
        self,
        fork_id: str,
        status: ForkStatus,
    ) -> bool:
        """Actualiza el estado de un fork."""
        completed_at = _ahora() if status in (ForkStatus.COMPLETED, ForkStatus.FAILED, ForkStatus.ABANDONED) else None
        return self._fork_store.update_status(fork_id, status.value, completed_at)

    def comparar_forks(
        self,
        fork_id_a: str,
        fork_id_b: str,
    ) -> dict[str, Any] | None:
        """Compara dos forks read-only.

        Devuelve un dict con las diferencias entre los estados finales
        de los dos forks.

        Args:
            fork_id_a: ID del primer fork.
            fork_id_b: ID del segundo fork.

        Returns:
            Dict con la comparación, o None si algún fork no existe.
        """
        fork_a = self._fork_store.get(fork_id_a)
        fork_b = self._fork_store.get(fork_id_b)

        if fork_a is None or fork_b is None:
            return None

        # Recuperar estados desde los checkpoints base de cada fork
        cp_a = self._checkpoint_store.get(fork_a.parent_checkpoint_id)
        cp_b = self._checkpoint_store.get(fork_b.parent_checkpoint_id)

        result: dict[str, Any] = {
            "fork_a": {
                "id": fork_a.id,
                "new_execution_id": fork_a.new_execution_id,
                "status": fork_a.status.value,
                "parent_checkpoint_id": fork_a.parent_checkpoint_id,
                "forked_at_seq": fork_a.forked_at_seq,
            },
            "fork_b": {
                "id": fork_b.id,
                "new_execution_id": fork_b.new_execution_id,
                "status": fork_b.status.value,
                "parent_checkpoint_id": fork_b.parent_checkpoint_id,
                "forked_at_seq": fork_b.forked_at_seq,
            },
            "differences": {},
        }

        if cp_a and cp_b:
            # Comparar semantic snapshots
            sem_a = cp_a.semantic.to_dict() if cp_a.semantic else {}
            sem_b = cp_b.semantic.to_dict() if cp_b.semantic else {}

            result["differences"]["semantic"] = {
                "same_intent": sem_a.get("intent") == sem_b.get("intent"),
                "same_confidence": sem_a.get("confidence") == sem_b.get("confidence"),
                "confidence_a": sem_a.get("confidence", 0.0),
                "confidence_b": sem_b.get("confidence", 0.0),
                "same_tasks_completed": sem_a.get("tasks_completed") == sem_b.get("tasks_completed"),
                "tasks_completed_a": sem_a.get("tasks_completed", 0),
                "tasks_completed_b": sem_b.get("tasks_completed", 0),
            }

            # Comparar stores_state keys
            keys_a = set(cp_a.stores_state.keys())
            keys_b = set(cp_b.stores_state.keys())
            result["differences"]["stores"] = {
                "same_keys": keys_a == keys_b,
                "keys_only_in_a": list(keys_a - keys_b),
                "keys_only_in_b": list(keys_b - keys_a),
                "common_keys": list(keys_a & keys_b),
            }

            # Comparar coordinator state
            coord_a = cp_a.stores_state.get("coordinator", {})
            coord_b = cp_b.stores_state.get("coordinator", {})
            if isinstance(coord_a, dict) and isinstance(coord_b, dict):
                result["differences"]["coordinator"] = {
                    "same_state": coord_a.get("estado") == coord_b.get("estado"),
                    "estado_a": coord_a.get("estado"),
                    "estado_b": coord_b.get("estado"),
                }

        result["same_parent"] = fork_a.parent_execution_id == fork_b.parent_execution_id
        result["same_checkpoint"] = fork_a.parent_checkpoint_id == fork_b.parent_checkpoint_id

        return result

    def obtener_fork_tree(self, execution_id: str) -> dict[str, Any]:
        """Construye el árbol de forks de una ejecución.

        Devuelve un dict con la jerarquía de forks anidados.
        """
        forks = self._fork_store.list_for_parent(execution_id)
        children: list[dict[str, Any]] = []
        for fork in forks:
            child_node = {
                "fork_id": fork.id,
                "new_execution_id": fork.new_execution_id,
                "status": fork.status.value,
                "forked_at_seq": fork.forked_at_seq,
                "new_objective": fork.new_objective,
                "children": [],
            }
            # Recursivamente buscar forks de la nueva ejecución
            sub_forks = self._fork_store.list_for_parent(fork.new_execution_id)
            if sub_forks:
                sub_tree = self.obtener_fork_tree(fork.new_execution_id)
                child_node["children"] = sub_tree.get("children", [])
            children.append(child_node)

        return {
            "execution_id": execution_id,
            "children": children,
        }
