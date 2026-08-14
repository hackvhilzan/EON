"""Tests de `eon.llm.provider` -- el punto único de entrada para obtener un
LLM por nombre (`eon.llm.provider.get_provider`/`get_provider_lazy`).

Deliberadamente NO instalan `anthropic`/`openai`/`google-generativeai`: en vez
de eso, monkeypatchean `provider._provider_classes` (la caché interna del
módulo) con una clase `LLM` de prueba, para probar la lógica de resolución de
nombre/entorno/kwargs sin tocar ningún SDK real. Los SDKs concretos se
prueban por separado en `test_providers_mock.py`.
"""

from __future__ import annotations

import pytest

from eon.llm import provider
from eon.llm.base import LLM


class _ProveedorFalso(LLM):
    """LLM de prueba: no importa ningún SDK, solo guarda los kwargs con los
    que se construyó y devuelve un eco del prompt."""

    name = "falso"

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def generate(self, prompt: str) -> str:
        return f"echo:{prompt}"


@pytest.fixture(autouse=True)
def _limpiar_cache_provider(monkeypatch):
    """`_load_provider_class` cachea la clase resuelta en `_provider_classes`
    (dict a nivel de módulo) -- se limpia entre tests para que un test no
    contamine al siguiente con una entrada falsa."""
    monkeypatch.setattr(provider, "_provider_classes", {})
    monkeypatch.delenv("EON_LLM_PROVIDER", raising=False)


def test_available_providers_lista_los_nombres_conocidos():
    assert provider.available_providers() == [
        "claude",
        "fallback",
        "gemini",
        "groq",
        "ollama",
        "openai",
        "openrouter",
    ]


def test_get_provider_nombre_desconocido_lanza_valueerror():
    with pytest.raises(ValueError, match="desconocido"):
        provider.get_provider("mistral")


def test_get_provider_usa_claude_por_defecto_sin_env_ni_nombre(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "claude", _ProveedorFalso)

    llm = provider.get_provider()

    assert isinstance(llm, _ProveedorFalso)


def test_get_provider_respeta_env_var_eon_llm_provider(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "openai", _ProveedorFalso)
    monkeypatch.setenv("EON_LLM_PROVIDER", "openai")

    llm = provider.get_provider()

    assert isinstance(llm, _ProveedorFalso)


def test_get_provider_nombre_explicito_gana_a_la_env_var(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "gemini", _ProveedorFalso)
    monkeypatch.setenv("EON_LLM_PROVIDER", "openai")

    llm = provider.get_provider("gemini")

    assert isinstance(llm, _ProveedorFalso)


def test_get_provider_pasa_kwargs_al_constructor(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "claude", _ProveedorFalso)

    llm = provider.get_provider("claude", model="modelo-x", temperature=0.1)

    assert llm.kwargs == {"model": "modelo-x", "temperature": 0.1}


def test_lazy_llm_no_construye_el_proveedor_real_hasta_generate(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "claude", _ProveedorFalso)

    llm = provider.get_provider_lazy()
    assert llm.name == "lazy"
    assert llm._real is None  # todavía no se construyó nada

    resultado = llm.generate("hola")

    assert resultado == "echo:hola"
    assert isinstance(llm._real, _ProveedorFalso)


def test_lazy_llm_reutiliza_la_misma_instancia_entre_llamadas(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "claude", _ProveedorFalso)

    llm = provider.get_provider_lazy()
    llm.generate("uno")
    primera_instancia = llm._real
    llm.generate("dos")

    assert llm._real is primera_instancia


def test_lazy_llm_propaga_provider_name_y_kwargs(monkeypatch):
    monkeypatch.setitem(provider._provider_classes, "openai", _ProveedorFalso)

    llm = provider.get_provider_lazy("openai", model="modelo-y")
    llm.generate("hola")

    assert isinstance(llm._real, _ProveedorFalso)
    assert llm._real.kwargs == {"model": "modelo-y"}
