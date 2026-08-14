"""
eon.llm.openai_provider
==========================
Implementación de LLM sobre la API de OpenAI (GPT).
Requiere el paquete `openai` y OPENAI_API_KEY (o pasar api_key explícitamente).
"""

import os

from .base import LLM

DEFAULT_MODEL = "gpt-4o"


class OpenAILLM(LLM):
    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        system: str | None = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ):
        import openai  # import perezoso

        self._client = openai.OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
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
