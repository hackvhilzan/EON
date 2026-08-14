from __future__ import annotations

import pytest

from eon.codeforces.cf_checker import CFChecker
from eon.verification.models import LayerResult, VerificationStatus


@pytest.fixture
def checker() -> CFChecker:
    return CFChecker()


def _verificar(checker: CFChecker, actual: str, expected: str, mode: str | None = None) -> LayerResult:
    evidence = {"actual": actual, "expected": expected}
    if mode is not None:
        evidence["mode"] = mode
    return checker.verificar(objective=None, evidence=evidence)


class TestCFCheckerEsUnVerifierReal:
    def test_devuelve_layer_result(self, checker: CFChecker):
        layer = _verificar(checker, "3\n", "3\n", mode="exact")
        assert isinstance(layer, LayerResult)
        assert layer.layer_name == "cf_checker"

    def test_status_passed_en_layer_result(self, checker: CFChecker):
        layer = _verificar(checker, "3\n", "3\n", mode="exact")
        assert layer.status == VerificationStatus.PASSED
        assert layer.passed

    def test_status_failed_en_layer_result(self, checker: CFChecker):
        layer = _verificar(checker, "4\n", "3\n", mode="exact")
        assert layer.status == VerificationStatus.FAILED
        assert not layer.passed


class TestCheckExact:
    def test_pasa_si_es_identico(self, checker: CFChecker):
        layer = _verificar(checker, "3\n", "3\n", mode="exact")
        assert layer.passed

    def test_falla_si_difiere_solo_en_espacios(self, checker: CFChecker):
        layer = _verificar(checker, "3\n", "3", mode="exact")
        assert not layer.passed
        assert "mismatch exacto" in layer.motivo


class TestCheckWhitespaceNormalized:
    def test_pasa_con_espacios_y_saltos_de_linea_distintos(self, checker: CFChecker):
        layer = _verificar(checker, "3  4\n5\n\n", "3 4\n5", mode="whitespace_normalized")
        assert layer.passed

    def test_pasa_por_defecto_sin_indicar_mode(self, checker: CFChecker):
        layer = _verificar(checker, " 1 2 3 \n", "1 2 3")
        assert layer.passed

    def test_falla_si_el_contenido_difiere(self, checker: CFChecker):
        layer = _verificar(checker, "3 4\n", "3 5\n", mode="whitespace_normalized")
        assert not layer.passed
        assert "mismatch normalizado" in layer.motivo


def test_modo_desconocido_lanza_value_error(checker: CFChecker):
    with pytest.raises(ValueError):
        _verificar(checker, "a", "a", mode="otro")
