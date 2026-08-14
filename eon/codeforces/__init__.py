"""
eon.codeforces
================
Solver de problemas de Codeforces, montado como capa mutable SOBRE los
ports del kernel (eon.sandbox, eon.tools, eon.verification) -- nunca
al revés: ningún módulo del kernel importa eon.codeforces (regla
verificada estáticamente en eon/tests/test_codeforces_boundary.py).

Hoy es standalone: fetch(internet_tool) -> solve(llm_tool) -> ejecuta
por SandboxExecutor -> verifica con CFChecker, con su propio loop de
reintentos (ver solver.py). No pasa por Coordinator/Planner/Scheduler.
`eon/codeforces/coordinator_binding.py` documenta el punto de enganche
para cuando eso cambie.
"""

from __future__ import annotations

from .cf_checker import CFChecker, CheckMode

__all__ = ["CFChecker", "CheckMode"]
