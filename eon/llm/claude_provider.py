"""
eon.llm.claude_provider
=========================
Implementación de LLM sobre la API de Anthropic (Claude).
Requiere el paquete `anthropic` y ANTHROPIC_API_KEY (o pasar api_key explícitamente).
"""

import os

from .base import LLM

DEFAULT_MODEL = "claude-sonnet-4-6"


class ClaudeLLM(LLM):
    name = "claude"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        system: str | None = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ):
        import anthropic  # import perezoso: no obliga a instalar el SDK si no se usa este proveedor

        self._client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.temperature = temperature

    def generate(self, prompt: str) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=self.system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
