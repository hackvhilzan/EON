"""
eon.llm.openrouter_provider
==============================
Implementación de LLM sobre la API de OpenRouter (https://openrouter.ai),
un gateway sobre decenas de modelos, con un catálogo rotativo de modelos
`:free` (sufijo en el nombre del modelo) a coste cero, sin tarjeta de
crédito para empezar. El catálogo `:free` cambia con el tiempo -- por eso
`DEFAULT_MODEL` es explícito y sustituible, nunca asumido fijo.

Requiere el paquete `openai` (OpenRouter expone un endpoint
OpenAI-compatible) y OPENROUTER_API_KEY (gratis en openrouter.ai, sin
tarjeta).
"""

import os

from .base import LLM

# Modelo por defecto con sufijo ":free" -- ver openrouter.ai/models?q=free
# para el catálogo vigente antes de confiar en este valor a largo plazo.
DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterLLM(LLM):
    name = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        system: str | None = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ):
        import openai  # import perezoso -- mismo SDK que openai_provider.py, endpoint distinto

        self._client = openai.OpenAI(
            api_key=api_key or os.environ["OPENROUTER_API_KEY"],
            base_url=DEFAULT_BASE_URL,
        )
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.temperature = temperature

    def generate(self, prompt: str) -> str:
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": prompt})

        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return resp.choices[0].message.content or ""
