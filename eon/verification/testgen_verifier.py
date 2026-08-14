"""
eon.verification.test_based_verifier
======================================
Layer 4: TestBasedVerifier.

Genera tests automáticos para el resultado y los ejecuta.
Si los tests pasan → cumple.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from .models import LayerResult, VerificationStatus

logger = logging.getLogger("eon.verification.test_based")


class TestGeneratorProtocol(Protocol):
    """Protocol para un generador de tests."""

    def generar_tests(self, criterio: str, artifact_content: str) -> list[str]:
        """Genera código de tests Python que verifican el criterio.

        Returns:
            Lista de strings con código de test ejecutable.
        """
        ...


class TestBasedVerifier:
    """Verifica resultados generando y ejecutando tests automáticos.

    Si el objetivo era "crea una función que ordene una lista",
    genera tests de ordenación y los ejecuta contra el artefacto.
    """

    __test__ = False  # evita que pytest intente coleccionar esta clase

    name = "test_based"

    def __init__(self, generator: TestGeneratorProtocol | None = None) -> None:
        self._generator = generator

    def verificar(
        self,
        objective: Any,
        evidence: dict[str, Any],
        artifacts: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LayerResult:
        if self._generator is None:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay generador de tests configurado",
            )

        if not artifacts:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay artefactos para testear",
            )

        criterio = ""
        if hasattr(objective, "criterio_de_exito"):
            criterio = objective.criterio_de_exito or ""
        elif isinstance(objective, str):
            criterio = objective
        elif isinstance(objective, dict):
            criterio = objective.get("criterio_de_exito", "")

        if not criterio:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay criterio_de_exito para generar tests",
            )

        # Generar y ejecutar tests para cada artefacto
        total_tests = 0
        passed_tests = 0
        failures: list[str] = []

        for artifact_name, artifact_content in artifacts.items():
            content_str = str(artifact_content)
            try:
                tests = self._generator.generar_tests(criterio, content_str)
            except Exception as exc:
                failures.append(f"Error generando tests para {artifact_name}: {exc}")
                continue

            for test_code in tests:
                total_tests += 1
                ok, msg = self._run_test(test_code, content_str)
                if ok:
                    passed_tests += 1
                else:
                    failures.append(f"{artifact_name}: {msg}")

        if total_tests == 0:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No se pudieron generar tests",
            )

        confidence = passed_tests / total_tests if total_tests > 0 else 0.0
        all_passed = passed_tests == total_tests

        return LayerResult(
            layer_name=self.name,
            status=VerificationStatus.PASSED if all_passed else VerificationStatus.FAILED,
            confidence=confidence,
            motivo=f"{passed_tests}/{total_tests} tests pasaron",
            details={
                "total_tests": total_tests,
                "passed_tests": passed_tests,
                "failures": failures[:5],
            },
        )

    @staticmethod
    def _run_test(test_code: str, artifact_content: str) -> tuple[bool, str]:
        """Ejecuta un test individual contra el artefacto."""
        import subprocess
        import sys

        wrapper = (
            "import sys, json\n"
            "_artifact = %r\n"
            "try:\n"
            "    exec(compile(%r, '<test>', 'exec'))\n"
            "    print(json.dumps({'ok': True}))\n"
            "except AssertionError as e:\n"
            "    print(json.dumps({'ok': False, 'error': str(e)}))\n"
            "    sys.exit(1)\n"
            "except Exception as e:\n"
            "    print(json.dumps({'ok': False, 'error': str(e)}))\n"
            "    sys.exit(1)\n"
        ) % (artifact_content, test_code)

        try:
            result = subprocess.run(
                [sys.executable, "-c", wrapper],
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr[:200] if result.stderr else "Test failed"
        except Exception as exc:
            return False, str(exc)
