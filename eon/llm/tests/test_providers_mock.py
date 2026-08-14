"""Tests de los tres proveedores concretos (`claude_provider.py`,
`openai_provider.py`, `gemini_provider.py`) sin instalar `anthropic`,
`openai` ni `google-generativeai`.

Cada `*_provider.py` importa su SDK de forma perezosa, dentro de
`__init__` (no a nivel de módulo) -- ver el comentario en cada archivo:
"import perezoso: no obliga a instalar el SDK si no se usa este proveedor".
Eso permite inyectar un módulo falso en `sys.modules` justo antes de
instanciar el proveedor, sin tocar el proveedor en sí ni requerir el SDK
real. Cada test comprueba dos cosas: que el cliente se construye con la API
key correcta, y que `generate()` extrae el texto de la forma de respuesta
específica de ese SDK.
"""

from __future__ import annotations

import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Claude (anthropic)
# ---------------------------------------------------------------------------


class _FakeAnthropicContentBlock:
    def __init__(self, type_: str, text: str):
        self.type = type_
        self.text = text


class _FakeAnthropicResponse:
    def __init__(self, content):
        self.content = content


class _FakeAnthropicMessages:
    def __init__(self, respuesta: str):
        self._respuesta = respuesta
        self.ultima_llamada: dict | None = None

    def create(self, **kwargs):
        self.ultima_llamada = kwargs
        return _FakeAnthropicResponse([_FakeAnthropicContentBlock("text", self._respuesta)])


class _FakeAnthropicClient:
    instancias: list[_FakeAnthropicClient] = []

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self.messages = _FakeAnthropicMessages("hola desde claude")
        _FakeAnthropicClient.instancias.append(self)


@pytest.fixture
def fake_anthropic_module(monkeypatch):
    _FakeAnthropicClient.instancias = []
    modulo = types.ModuleType("anthropic")
    modulo.Anthropic = _FakeAnthropicClient
    monkeypatch.setitem(sys.modules, "anthropic", modulo)
    return modulo


def test_claude_provider_usa_api_key_explicita(fake_anthropic_module):
    from eon.llm.claude_provider import ClaudeLLM

    ClaudeLLM(api_key="sk-explicita")

    assert _FakeAnthropicClient.instancias[-1].api_key == "sk-explicita"


def test_claude_provider_usa_env_var_si_no_hay_api_key_explicita(fake_anthropic_module, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-del-entorno")
    from eon.llm.claude_provider import ClaudeLLM

    ClaudeLLM()

    assert _FakeAnthropicClient.instancias[-1].api_key == "sk-del-entorno"


def test_claude_provider_generate_extrae_solo_bloques_de_texto(fake_anthropic_module, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from eon.llm.claude_provider import ClaudeLLM

    llm = ClaudeLLM(model="claude-x", system="eres útil")
    llm._client.messages = _FakeAnthropicMessages("hola desde claude")
    # bloque no-texto (p. ej. tool_use) no debe colarse en el resultado
    llm._client.messages.create = lambda **kwargs: _FakeAnthropicResponse(
        [
            _FakeAnthropicContentBlock("tool_use", "ignorar-esto"),
            _FakeAnthropicContentBlock("text", "hola "),
            _FakeAnthropicContentBlock("text", "desde claude"),
        ]
    )

    resultado = llm.generate("saluda")

    assert resultado == "hola desde claude"


def test_claude_provider_envia_modelo_y_prompt_correctos(fake_anthropic_module, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from eon.llm.claude_provider import ClaudeLLM

    llm = ClaudeLLM(model="claude-x", system="eres útil", max_tokens=50, temperature=0.2)
    llm.generate("saluda")

    llamada = llm._client.messages.ultima_llamada
    assert llamada["model"] == "claude-x"
    assert llamada["system"] == "eres útil"
    assert llamada["max_tokens"] == 50
    assert llamada["temperature"] == 0.2
    assert llamada["messages"] == [{"role": "user", "content": "saluda"}]


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class _FakeOpenAIMessage:
    def __init__(self, content: str | None):
        self.content = content


class _FakeOpenAIChoice:
    def __init__(self, content: str | None):
        self.message = _FakeOpenAIMessage(content)


class _FakeOpenAICompletionResponse:
    def __init__(self, content: str | None):
        self.choices = [_FakeOpenAIChoice(content)]


class _FakeOpenAICompletions:
    def __init__(self, respuesta: str | None):
        self._respuesta = respuesta
        self.ultima_llamada: dict | None = None

    def create(self, **kwargs):
        self.ultima_llamada = kwargs
        return _FakeOpenAICompletionResponse(self._respuesta)


class _FakeOpenAIChat:
    def __init__(self, respuesta: str | None):
        self.completions = _FakeOpenAICompletions(respuesta)


class _FakeOpenAIClient:
    instancias: list[_FakeOpenAIClient] = []

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self.chat = _FakeOpenAIChat("hola desde gpt")
        _FakeOpenAIClient.instancias.append(self)


@pytest.fixture
def fake_openai_module(monkeypatch):
    _FakeOpenAIClient.instancias = []
    modulo = types.ModuleType("openai")
    modulo.OpenAI = _FakeOpenAIClient
    monkeypatch.setitem(sys.modules, "openai", modulo)
    return modulo


def test_openai_provider_usa_api_key_explicita(fake_openai_module):
    from eon.llm.openai_provider import OpenAILLM

    OpenAILLM(api_key="sk-explicita")

    assert _FakeOpenAIClient.instancias[-1].api_key == "sk-explicita"


def test_openai_provider_sin_system_no_antepone_mensaje_system(fake_openai_module, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from eon.llm.openai_provider import OpenAILLM

    llm = OpenAILLM()
    llm.generate("hola")

    mensajes = llm._client.chat.completions.ultima_llamada["messages"]
    assert mensajes == [{"role": "user", "content": "hola"}]


def test_openai_provider_con_system_antepone_mensaje_system(fake_openai_module, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from eon.llm.openai_provider import OpenAILLM

    llm = OpenAILLM(system="eres útil")
    llm.generate("hola")

    mensajes = llm._client.chat.completions.ultima_llamada["messages"]
    assert mensajes == [
        {"role": "system", "content": "eres útil"},
        {"role": "user", "content": "hola"},
    ]


def test_openai_provider_generate_devuelve_el_contenido_del_choice(fake_openai_module, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from eon.llm.openai_provider import OpenAILLM

    llm = OpenAILLM()
    resultado = llm.generate("hola")

    assert resultado == "hola desde gpt"


def test_openai_provider_content_none_devuelve_cadena_vacia(fake_openai_module, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from eon.llm.openai_provider import OpenAILLM

    llm = OpenAILLM()
    llm._client.chat.completions._respuesta = None

    assert llm.generate("hola") == ""


# ---------------------------------------------------------------------------
# Gemini (google.generativeai)
# ---------------------------------------------------------------------------


class _FakeGeminiResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeGenerativeModel:
    instancias: list[_FakeGenerativeModel] = []

    def __init__(self, model, system_instruction=None):
        self.model = model
        self.system_instruction = system_instruction
        self.ultima_llamada: dict | None = None
        _FakeGenerativeModel.instancias.append(self)

    def generate_content(self, prompt, generation_config=None):
        self.ultima_llamada = {"prompt": prompt, "generation_config": generation_config}
        return _FakeGeminiResponse("hola desde gemini")


@pytest.fixture
def fake_genai_module(monkeypatch):
    _FakeGenerativeModel.instancias = []
    llamadas_configure: list[dict] = []

    modulo = types.ModuleType("google.generativeai")
    modulo.configure = lambda **kwargs: llamadas_configure.append(kwargs)
    modulo.GenerativeModel = _FakeGenerativeModel
    modulo._llamadas_configure = llamadas_configure

    monkeypatch.setitem(sys.modules, "google.generativeai", modulo)
    # 'google' es un namespace package real en este entorno; nos aseguramos
    # de que el atributo también quede accesible como google.generativeai,
    # que es como `import google.generativeai as genai` lo resuelve.
    import google

    monkeypatch.setattr(google, "generativeai", modulo, raising=False)
    return modulo


def test_gemini_provider_usa_api_key_explicita(fake_genai_module):
    from eon.llm.gemini_provider import GeminiLLM

    GeminiLLM(api_key="clave-explicita")

    assert fake_genai_module._llamadas_configure[-1] == {"api_key": "clave-explicita"}


def test_gemini_provider_usa_env_var_si_no_hay_api_key_explicita(fake_genai_module, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "clave-del-entorno")
    from eon.llm.gemini_provider import GeminiLLM

    GeminiLLM()

    assert fake_genai_module._llamadas_configure[-1] == {"api_key": "clave-del-entorno"}


def test_gemini_provider_pasa_modelo_y_system_instruction(fake_genai_module, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "clave-test")
    from eon.llm.gemini_provider import GeminiLLM

    GeminiLLM(model="gemini-x", system="eres útil")

    instancia = _FakeGenerativeModel.instancias[-1]
    assert instancia.model == "gemini-x"
    assert instancia.system_instruction == "eres útil"


def test_gemini_provider_generate_devuelve_el_texto_de_la_respuesta(fake_genai_module, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "clave-test")
    from eon.llm.gemini_provider import GeminiLLM

    llm = GeminiLLM(max_tokens=123, temperature=0.5)
    resultado = llm.generate("hola")

    assert resultado == "hola desde gemini"
    llamada = llm._client.ultima_llamada
    assert llamada["prompt"] == "hola"
    assert llamada["generation_config"] == {"max_output_tokens": 123, "temperature": 0.5}
