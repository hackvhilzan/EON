"""
eon.codeforces.cf_checker
============================
CFChecker -- primer verifier de runtime real de eon.codeforces.

Implementa la interfaz de layer que ya usan las capas de
eon.verification (ver eon/verification/criteria_verifier.py,
structural_verifier.py, etc.): un atributo `name` y un método
`verificar(objective, evidence, artifacts, **kwargs) -> LayerResult`.
Cumplir esa interfaz -- no una propia -- es lo que permite que, más
adelante, una instancia de CFChecker se añada como layer más de un
CompositeVerifier real sin adaptar nada (ver
eon/codeforces/coordinator_binding.py).

Compara el stdout de una solución contra el output esperado de un
sample de Codeforces, en dos modos:
- "exact": comparación byte a byte.
- "whitespace_normalized" (por defecto): strip por línea + colapsar
  espacios internos, ignorando líneas en blanco finales -- el modo
  que usa el juez real de Codeforces para la mayoría de problemas.
"""

from __future__ import annotations

from typing import Any, Literal

from ..verification.models import LayerResult, VerificationStatus

CheckMode = Literal["exact", "whitespace_normalized"]


def _normalizar(texto: str) -> str:
    lineas = [" ".join(linea.split()) for linea in texto.replace("\r\n", "\n").split("\n")]
    while lineas and lineas[-1] == "":
        lineas.pop()
    return "\n".join(lineas)


class CFChecker:
    """Verifica el stdout de una solución CF contra el output esperado.

    Uso directo (fuera de un CompositeVerifier), como hace
    eon.codeforces.solver por cada sample:

        checker = CFChecker()
        layer = checker.verificar(
            objective=None,
            evidence={"actual": stdout, "expected": expected, "mode": "exact"},
        )
        if not layer.passed:
            print(layer.motivo)
    """

    name = "cf_checker"

    def verificar(
        self,
        objective: Any,
        evidence: dict[str, Any],
        artifacts: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LayerResult:
        actual = evidence.get("actual", "")
        expected = evidence.get("expected", "")
        mode: CheckMode = evidence.get("mode") or kwargs.get("mode", "whitespace_normalized")

        if mode == "exact":
            passed = actual == expected
            motivo = "coincidencia exacta" if passed else f"mismatch exacto: esperado={expected!r} obtenido={actual!r}"
        elif mode == "whitespace_normalized":
            norm_actual = _normalizar(actual)
            norm_expected = _normalizar(expected)
            passed = norm_actual == norm_expected
            motivo = (
                "coincidencia tras normalizar espacios"
                if passed
                else f"mismatch normalizado: esperado={norm_expected!r} obtenido={norm_actual!r}"
            )
        else:
            raise ValueError(f"modo de comparación desconocido: {mode!r}")

        return LayerResult(
            layer_name=self.name,
            status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            confidence=1.0 if passed else 0.0,
            motivo=motivo,
        )
