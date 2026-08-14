"""
eon.llm.groq_provider
========================
Implementación de LLM sobre la API de Groq (https://groq.com), modelos
open-source (Llama, Qwen, GPT-OSS...) servidos en su hardware LPU. Free
tier real sin tarjeta de crédito (con límites de requests/día, no de
"crédito" que se agota silenciosamente).

Requiere el paquete `openai` (Groq expone un endpoint OpenAI-compatible,
no hace falta un SDK propio) y GROQ_API_KEY (gratis en console.groq.com,
sin tarjeta).
"""

import os

from .base import LLM

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


class GroqLLM(LLM):
    name = "groq"

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
            api_key=api_key or os.environ["GROQ_API_KEY"],
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
