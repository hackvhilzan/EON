"""Tests de `eon.llm.fallback_provider.FallbackLLM` -- la cadena que salta
al siguiente proveedor cuando el actual falla (rate-limit, créditos
agotados, caído). Mismo criterio que `test_provider.py`: se monkeypatchea
`provider._provider_classes` con proveedores de prueba, sin tocar ningún
SDK real.
"""

from __future__ import annotations

import pytest

from eon.llm import provider
from eon.llm.base import LLM
from eon.llm.fallback_provider import DEFAULT_CHAIN, AllProvidersFailedError, FallbackLLM


class _ProveedorQueFalla(LLM):
    name = "que_falla"

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def generate(self, prompt: str) -> str:
        raise RuntimeError("429: límite de tasa alcanzado")


class _ProveedorQueFuncionaEco(LLM):
    name = "que_funciona"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.llamadas: list[str] = []

    def generate(self, prompt: str) -> str:
        self.llamadas.append(prompt)
        return f"echo:{prompt}"


class _ProveedorQueNoConstruye(LLM):
    name = "que_no_construye"

    def __init__(self, **kwargs):
        raise KeyError("API_KEY_AUSENTE")

    def generate(self, prompt: str) -> str:  # pragma: no cover -- nunca se llega aquí
        raise AssertionError("no debería construirse")


@pytest.fixture(autouse=True)
def _limpiar_cache_provider(monkeypatch):
    monkeypatch.setattr(provider, "_provider_classes", {})
    monkeypatch.delenv("EON_LLM_FALLBACK_CHAIN", raising=False)


def test_chain_por_defecto_sin_env_ni_argumento():
    llm = FallbackLLM()
    assert llm._chain == DEFAULT_CHAIN


def test_chain_explicita_gana_a_la_env_var(monkeypatch):
    monkeypatch.setenv("EON_LLM_FALLBACK_CHAIN", "groq,openrouter")
    llm = FallbackLLM(chain=["ollama", "claude"])
    assert llm._chain == ["ollama", "claude"]


def test_chain_desde_env_var_cuando_no_hay_argumento(monkeypatch):
    monkeypatch.setenv("EON_LLM_FALLBACK_CHAIN", "groq, openrouter , claude")
    llm = FallbackLLM()
    assert llm._chain == ["groq", "openrouter", "claude"]


def test_primer_proveedor_exitoso_responde_sin_probar_los_siguientes(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "a", _ProveedorQueFuncionaEco)
    monkeypatch.setitem(provider._provider_classes, "b", _ProveedorQueFalla)

    llm = FallbackLLM(chain=["a", "b"])
    resultado = llm.generate("hola")

    assert resultado == "echo:hola"
    assert "b" not in llm._instancias  # nunca se llegó a instanciar el segundo


def test_salta_al_siguiente_si_el_primero_falla_en_generate(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "a", _ProveedorQueFalla)
    monkeypatch.setitem(provider._provider_classes, "b", _ProveedorQueFuncionaEco)

    llm = FallbackLLM(chain=["a", "b"])
    resultado = llm.generate("hola")

    assert resultado == "echo:hola"


def test_salta_al_siguiente_si_el_primero_falla_al_construirse(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "a", _ProveedorQueNoConstruye)
    monkeypatch.setitem(provider._provider_classes, "b", _ProveedorQueFuncionaEco)

    llm = FallbackLLM(chain=["a", "b"])
    resultado = llm.generate("hola")

    assert resultado == "echo:hola"


def test_todos_fallan_lanza_all_providers_failed_con_cada_error(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "a", _ProveedorQueFalla)
    monkeypatch.setitem(provider._provider_classes, "b", _ProveedorQueNoConstruye)

    llm = FallbackLLM(chain=["a", "b"])
    with pytest.raises(AllProvidersFailedError) as exc_info:
        llm.generate("hola")

    assert set(exc_info.value.errores.keys()) == {"a", "b"}


def test_instancia_exitosa_se_reutiliza_entre_llamadas(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "a", _ProveedorQueFuncionaEco)

    llm = FallbackLLM(chain=["a"])
    llm.generate("uno")
    instancia_a = llm._instancias["a"]
    llm.generate("dos")

    assert llm._instancias["a"] is instancia_a
    assert instancia_a.llamadas == ["uno", "dos"]


def test_provider_kwargs_se_pasan_al_proveedor_correspondiente(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "a", _ProveedorQueFuncionaEco)

    llm = FallbackLLM(chain=["a"], provider_kwargs={"a": {"model": "modelo-x"}})
    llm.generate("hola")

    assert llm._instancias["a"].kwargs == {"model": "modelo-x"}
