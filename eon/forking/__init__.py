"""
eon.forking
==============
Execution Forking: ramificar una ejecución desde un checkpoint.

Permite explorar alternativas sin perder la ejecución original. La
ejecución forked hereda el estado del checkpoint pero diverge desde ahí.

Diferenciador: permite "qué pasaría si" con ejecuciones reales — fork
desde el punto donde un plan diverge, probar dos estrategias, comparar
resultados.

Semántica:
- fork_from_checkpoint(checkpoint_id) crea una nueva CoordinatorExecution
- El estado del checkpoint se copia (deep copy) con nuevos IDs
- La ejecución original no se muta
- Se preservan metadatos del parent en un registro ExecutionFork durable
- Multiple forks pueden correr en paralelo
- comparar_forks() hace un diff read-only de estados finales
"""

from __future__ import annotations

from .manager import ForkManager
from .models import ExecutionFork, ForkStatus
from .store import SQLiteForkStore

__all__ = [
    "ExecutionFork",
    "ForkStatus",
    "ForkManager",
    "SQLiteForkStore",
]
