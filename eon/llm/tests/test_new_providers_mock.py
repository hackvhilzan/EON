"""Tests de los tres proveedores nuevos (`ollama_provider.py`,
`groq_provider.py`, `openrouter_provider.py`) sin instalar `httpx`/`openai`
reales -- mismo patrón que `test_providers_mock.py`: se inyecta un módulo
falso en `sys.modules` justo antes de instanciar el proveedor.
"""

from __future__ import annotations

import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Ollama (httpx directo, sin SDK)
# ---------------------------------------------------------------------------


class _FakeOllamaResponse:
    def __init__(self, contenido: str):
        self._contenido = contenido
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self._contenido}}


class _FakeOllamaClient:
    instancias: list[_FakeOllamaClient] = []

    def __init__(self, timeout: float | None = None):
        self.timeout = timeout
        self._respuesta = "hola desde ollama"
        self.ultima_llamada: dict | None = None
        _FakeOllamaClient.instancias.append(self)

    def post(self, url, json):
        self.ultima_llamada = {"url": url, "json": json}
        return _FakeOllamaResponse(self._respuesta)


@pytest.fixture
def fake_httpx_module(monkeypatch):
    _FakeOllamaClient.instancias = []
    modulo = types.ModuleType("httpx")
    modulo.Client = _FakeOllamaClient
    monkeypatch.setitem(sys.modules, "httpx", modulo)
    return modulo


def test_ollama_provider_usa_base_url_por_defecto_sin_env(fake_httpx_module, monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    from eon.llm.ollama_provider import OllamaLLM

    llm = OllamaLLM()
    llm.generate("hola")

    assert llm._client.ultima_llamada["url"] == "http://localhost:11434/api/chat"


def test_ollama_provider_respeta_ollama_base_url_env(fake_httpx_module, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://otra-maquina:11434")
    from eon.llm.ollama_provider import OllamaLLM

    llm = OllamaLLM()
    llm.generate("hola")

    assert llm._client.ultima_llamada["url"] == "http://otra-maquina:11434/api/chat"


def test_ollama_provider_no_exige_ninguna_api_key(fake_httpx_module, monkeypatch):
    # A diferencia de claude/openai/gemini, no debe fallar por falta de
    # ninguna variable de entorno de credenciales -- es local, sin key.
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from eon.llm.ollama_provider import OllamaLLM

    OllamaLLM()  # no debe lanzar


def test_ollama_provider_con_system_antepone_mensaje_system(fake_httpx_module):
    from eon.llm.ollama_provider import OllamaLLM

    llm = OllamaLLM(system="eres útil")
    llm.generate("hola")

    mensajes = llm._client.ultima_llamada["json"]["messages"]
    assert mensajes == [
        {"role": "system", "content": "eres útil"},
        {"role": "user", "content": "hola"},
    ]


def test_ollama_provider_generate_devuelve_el_contenido(fake_httpx_module):
    from eon.llm.ollama_provider import OllamaLLM

    llm = OllamaLLM()
    resultado = llm.generate("hola")

    assert resultado == "hola desde ollama"


# ---------------------------------------------------------------------------
# Groq y OpenRouter (endpoint OpenAI-compatible, mismo SDK `openai`)
# ---------------------------------------------------------------------------


class _FakeOpenAICompatMessage:
    def __init__(self, content: str | None):
        self.content = content


class _FakeOpenAICompatChoice:
    def __init__(self, content: str | None):
        self.message = _FakeOpenAICompatMessage(content)


class _FakeOpenAICompatResponse:
    def __init__(self, content: str | None):
        self.choices = [_FakeOpenAICompatChoice(content)]


class _FakeOpenAICompatCompletions:
    def __init__(self, respuesta: str | None):
        self._respuesta = respuesta
        self.ultima_llamada: dict | None = None

    def create(self, **kwargs):
        self.ultima_llamada = kwargs
        return _FakeOpenAICompatResponse(self._respuesta)


class _FakeOpenAICompatChat:
    def __init__(self, respuesta: str | None):
        self.completions = _FakeOpenAICompatCompletions(respuesta)


class _FakeOpenAICompatClient:
    instancias: list[_FakeOpenAICompatClient] = []

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.chat = _FakeOpenAICompatChat("hola desde el proveedor")
        _FakeOpenAICompatClient.instancias.append(self)


@pytest.fixture
def fake_openai_sdk_module(monkeypatch):
    _FakeOpenAICompatClient.instancias = []
    modulo = types.ModuleType("openai")
    modulo.OpenAI = _FakeOpenAICompatClient
    monkeypatch.setitem(sys.modules, "openai", modulo)
    return modulo


def test_groq_provider_usa_base_url_de_groq(fake_openai_sdk_module, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    from eon.llm.groq_provider import GroqLLM

    GroqLLM()

    assert _FakeOpenAICompatClient.instancias[-1].base_url == "https://api.groq.com/openai/v1"
    assert _FakeOpenAICompatClient.instancias[-1].api_key == "gsk-test"


def test_groq_provider_generate_devuelve_el_contenido(fake_openai_sdk_module, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    from eon.llm.groq_provider import GroqLLM

    llm = GroqLLM()
    assert llm.generate("hola") == "hola desde el proveedor"


def test_openrouter_provider_usa_base_url_de_openrouter(fake_openai_sdk_module, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    from eon.llm.openrouter_provider import OpenRouterLLM

    OpenRouterLLM()

    assert _FakeOpenAICompatClient.instancias[-1].base_url == "https://openrouter.ai/api/v1"
    assert _FakeOpenAICompatClient.instancias[-1].api_key == "or-test"


def test_openrouter_provider_usa_modelo_free_por_defecto(fake_openai_sdk_module, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    from eon.llm.openrouter_provider import OpenRouterLLM

    llm = OpenRouterLLM()
    llm.generate("hola")

    assert llm._client.chat.completions.ultima_llamada["model"].endswith(":free")
