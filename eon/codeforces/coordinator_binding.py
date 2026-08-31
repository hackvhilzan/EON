"""
eon.codeforces.coordinator_binding
====================================
Seam de evolución: punto de enganche -- documentado, no implementado
del todo -- para que eon.codeforces se registre como capability +
verifier layer del Coordinator, sin que el kernel llegue nunca a
importar eon.codeforces (ver eon/tests/test_codeforces_boundary.py).

Estado actual: eon.codeforces es standalone (ver solver.py) --
fetch/solve/ejecuta/verifica en su propio loop de reintentos, sin
pasar por Coordinator/Planner/Scheduler. Este módulo deja preparado el
punto de enganche para la vía "drop-in": que el Coordinator dispare el
solver como una Task más y que CFChecker se sume como layer de un
CompositeVerifier real, sin reescribir nada de cf_checker.py ni de
solver.py.

TODO (implementación futura, fuera de este commit):
1. register_cf_capability(): envolver `solver.solve_problem` en una
   Tool (eon.tools.base_tool.Tool) y registrarla en
   eon.capabilities.CAPABILITY_MAP vía register_capability(), bajo un
   capability_id tipo "tool.codeforces_solve". Hoy solve_problem es
   una función standalone, no una Tool -- el loop no pasa por
   CapabilityExecutor/ToolRegistry (ver solver.py) -- así que este es
   el trabajo real pendiente, no un simple registro.
2. Decidir si el Coordinator dispara el loop de solver.py como una
   única Task de larga duración, o si el retry-loop se remodela como
   Tasks separadas del Planner -- hoy solver.py asume lo primero, y
   esa decisión cambia la forma de (1).

as_verifier_layer() de abajo SÍ está implementado: CFChecker ya
cumple la interfaz de layer (`name` + `verificar(...) -> LayerResult`)
sin necesitar ningún adaptador.
"""

from __future__ import annotations

from .cf_checker import CFChecker


def as_verifier_layer() -> CFChecker:
    """Devuelve un CFChecker listo para añadirse a un CompositeVerifier
    real, p. ej.:

        from eon.verification.composite_verifier import CompositeVerifier
        from eon.codeforces.coordinator_binding import as_verifier_layer

        composite = CompositeVerifier()
        composite.add_layer(as_verifier_layer())

    CFChecker ya cumple la interfaz de layer -- esta función solo
    documenta el punto de enganche.
    """
    return CFChecker()


def register_cf_capability() -> None:
    """TODO (no implementado): registrar una capability CF en
    eon.capabilities.CAPABILITY_MAP para que el Coordinator pueda
    invocar el solver como una Task más. Ver el punto 1 del docstring
    del módulo -- requiere envolver solve_problem en una Tool primero.
    """
    raise NotImplementedError(
        "register_cf_capability es el punto de enganche documentado, "
        "no implementado todavía -- ver TODO en el docstring del módulo."
    )
