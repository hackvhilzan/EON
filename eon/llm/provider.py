"""
eon.llm.provider
===================
Punto único de entrada para obtener un LLM por nombre (o por configuración vía
EON_LLM_PROVIDER). El resto de EON debe importar SIEMPRE desde aquí, nunca instanciar
un *_provider.py directamente, para poder cambiar de modelo con solo tocar una variable
de entorno o un argumento.

Uso:
    from eon.llm.provider import get_provider
    llm = get_provider()  # usa EON_LLM_PROVIDER, por defecto "claude"
    print(llm.generate("Hola"))
"""

import os

from .base import LLM

_KNOWN_PROVIDERS = {"claude", "openai", "gemini", "ollama", "groq", "openrouter", "fallback"}
_provider_classes: dict[str, type[LLM]] = {}


def _load_provider_class(name: str) -> type[LLM]:
    if name in _provider_classes:
        return _provider_classes[name]

    if name == "claude":
        from .claude_provider import ClaudeLLM as cls
    elif name == "openai":
        from .openai_provider import OpenAILLM as cls
    elif name == "gemini":
        from .gemini_provider import GeminiLLM as cls
    elif name == "ollama":
        # Open-source, local, sin API key ni límite de tokens/créditos --
        # el único límite real es el hardware donde corre Ollama.
        from .ollama_provider import OllamaLLM as cls
    elif name == "groq":
        # Free tier real sin tarjeta (limitado por requests/día, no por
        # "crédito" que se agota de forma opaca).
        from .groq_provider import GroqLLM as cls
    elif name == "openrouter":
        # Free tier real sin tarjeta, catálogo de modelos ":free" rotativo.
        from .openrouter_provider import OpenRouterLLM as cls
    elif name == "fallback":
        # No es un proveedor real: envuelve una cadena de los de arriba y
        # salta al siguiente si el actual falla (rate-limit, créditos,
        # caído). Ver `fallback_provider.py`.
        from .fallback_provider import FallbackLLM as cls
    else:
        raise ValueError(f"Proveedor de LLM desconocido: '{name}'. Opciones: {sorted(_KNOWN_PROVIDERS)}")

    _provider_classes[name] = cls
    return cls


class LazyLLM(LLM):
    """Envuelve a get_provider() pero difiere la construcción real del proveedor
    hasta la primera llamada a generate(). Existe para que Core() se pueda
    instanciar (al importar eon.interfaces.fastapi_app, en tests, en /health)
    sin exigir credenciales de un proveedor real desde el arranque del proceso.
    El error de configuración (API key ausente, SDK no instalado) solo aparece
    cuando de verdad se necesita generar texto, no antes. Lo usa por defecto
    `eon.capabilities.CapabilityExecutor` (el punto de integración con el
    Kernel, ver `eon/CAPABILITIES.md`) para no exigir credenciales de un
    proveedor real desde el arranque del proceso."""

    name = "lazy"

    def __init__(self, provider_name: str | None = None, **kwargs):
        self._provider_name = provider_name
        self._kwargs = kwargs
        self._real: LLM | None = None

    def generate(self, prompt: str) -> str:
        if self._real is None:
            self._real = get_provider(self._provider_name, **self._kwargs)
        return self._real.generate(prompt)


def get_provider(name: str | None = None, **kwargs) -> LLM:
    """Devuelve una instancia lista para usar (con método .generate(prompt)) del proveedor pedido.

    Args:
        name: 'claude' | 'openai' | 'gemini'. Si se omite, usa EON_LLM_PROVIDER o 'claude'.
        **kwargs: se pasan al constructor del proveedor (ej. api_key, model, system).
    """
    resolved_name = name or os.environ.get("EON_LLM_PROVIDER", "claude")
    cls = _load_provider_class(resolved_name)
    return cls(**kwargs)


def available_providers() -> list[str]:
    return sorted(_KNOWN_PROVIDERS)


def get_provider_lazy(name: str | None = None, **kwargs) -> LLM:
    """Como get_provider(), pero no construye el proveedor real hasta el primer
    generate()."""
    return LazyLLM(name, **kwargs)
