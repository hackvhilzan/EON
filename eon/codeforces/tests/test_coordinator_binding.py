from __future__ import annotations

import pytest

from ..cf_checker import CFChecker
from ..coordinator_binding import as_verifier_layer, register_cf_capability


def test_as_verifier_layer_devuelve_un_cf_checker_usable():
    layer = as_verifier_layer()
    assert isinstance(layer, CFChecker)
    # cumple la interfaz de layer sin adaptador: name + verificar(...)
    resultado = layer.verificar(objective=None, evidence={"actual": "3\n", "expected": "3\n"})
    assert resultado.passed


def test_register_cf_capability_es_un_stub_documentado():
    with pytest.raises(NotImplementedError):
        register_cf_capability()
