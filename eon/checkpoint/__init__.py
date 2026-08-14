"""
eon.checkpoint
================
Checkpointing y Semantic Snapshot para EON.

Un Checkpoint captura el estado completo de una ejecución en un instante:
- Estado técnico de los 7 stores (objective, plan, scheduler, workspace, package, worker, coordinator)
- Artefactos producidos
- Coste acumulado
- Hash de integridad verificable
- Anclado al EventStore via event_seq

SemanticSnapshot captura el "por qué" del estado, no solo el "qué":
- Intención, hipótesis, restricciones, riesgos, evidencia
- Decisión pendiente, confianza, coste, narrativa

Recuperación: cargar el último checkpoint válido y replay de eventos
posteriores a event_seq.
"""
from __future__ import annotations

from .manager import CheckpointManager
from .models import Checkpoint, CheckpointKind, SemanticSnapshot
from .semantic import SemanticSnapshotBuilder
from .store import SQLiteCheckpointStore

__all__ = [
    "Checkpoint",
    "CheckpointKind",
    "SemanticSnapshot",
    "SQLiteCheckpointStore",
    "SemanticSnapshotBuilder",
    "CheckpointManager",
]
