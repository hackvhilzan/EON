"""
eon.objectives.verifier
==========================
Verifica únicamente si un Objetivo cumple su `criterio_de_exito`, y con qué
confianza (OBJECTIVES.md §8, operación `verificar`). No sabe nada del Planner ni
del Scheduler -- Invariante 13: la evaluación nunca depende de la implementación
concreta de una Capacidad, una Tool o un proveedor LLM. Análogo a
`Core.verificar_resultados`, pero desacoplado del ciclo de Task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .models import Objective


@dataclass
class VerificationResult:
    cumple: bool
    confianza: float
    motivo: str = ""


class ResultadoJudge(Protocol):
    """Lo único que Verifier necesita del mundo exterior: algo capaz de juzgar un
    resultado de ejecución contra un criterio de éxito en texto libre y devolver
    (cumple, confianza). En producción esto lo implementa un LLM; en tests, un
    stub determinista. Verifier nunca importa un proveedor LLM concreto -- eso
    respetaría la frontera que la Invariante 13 prohíbe cruzar."""

    def juzgar(self, criterio_de_exito: str, resultado: Any) -> tuple[bool, float]: ...


class Verifier:
    def __init__(self, judge: ResultadoJudge | None = None):
        self._judge = judge

    def verificar(self, objective: Objective, resultado: Any = None) -> VerificationResult:
        """Contra el `criterio_de_exito` fijado al crear el Objetivo (Invariante
        7) -- nunca contra un criterio inventado después de ver el resultado.
        `verificar` es la única operación que puede llevar a `completado`, y solo
        si `confianza >= confianza_minima` (§9.2)."""
        if self._judge is not None:
            cumple, confianza = self._judge.juzgar(objective.criterio_de_exito, resultado)
        elif isinstance(resultado, tuple) and len(resultado) == 2:
            # Sin judge inyectado: se acepta un (cumple, confianza) ya evaluado --
            # útil en tests que no necesitan razonamiento real de un LLM.
            cumple, confianza = resultado
        else:
            # Ni judge ni resultado ya evaluado: por seguridad, nunca se asume
            # cumplido (un Objetivo no se completa "por defecto").
            cumple, confianza = False, 0.0

        confianza = float(confianza)
        if cumple and confianza < objective.confianza_minima:
            cumple = False  # §9.2: ejecutado sin errores no basta si no alcanza confianza_minima

        motivo = "" if cumple else "no cumple criterio_de_exito o confianza por debajo de confianza_minima"
        return VerificationResult(cumple=cumple, confianza=confianza, motivo=motivo)
