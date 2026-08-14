"""
eon.timetravel
================
Time Travel: reconstrucción read-only del estado del kernel en cualquier
event_seq del EventStore.

Diferenciador clave: ningún competidor permite navegar arbitrariamente por
el tiempo de ejecución. Con checkpointing + event sourcing, EON reconstruye
el estado exacto en cualquier punto.

Filosofía de reconstrucción (honestidad sobre completitud):
- Si existe un checkpoint con event_seq <= target_seq, el estado base es
  exacto (checkpoint verificado con hash SHA-256).
- Los eventos entre el checkpoint y target_seq se adjuntan como timeline.
- Si no hay checkpoint base, la reconstrucción es parcial (complete=False).
- Se registran applied_events vs unapplied_events para transparencia.
- La reconstrucción es read-only: nunca muta stores reales.
"""

from __future__ import annotations

from .machine import TimeMachine
from .models import ReconstructedState, ReconstructionMode

__all__ = [
    "TimeMachine",
    "ReconstructedState",
    "ReconstructionMode",
]
