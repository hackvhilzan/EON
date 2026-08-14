"""
eon.llm.gemini_provider
==========================
Implementación de LLM sobre la API de Google (Gemini).
Requiere el paquete `google-generativeai` y GOOGLE_API_KEY (o pasar api_key explícitamente).
"""

import os

from .base import LLM

DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiLLM(LLM):
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        system: str | None = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ):
        import google.generativeai as genai  # import perezoso

        genai.configure(api_key=api_key or os.environ["GOOGLE_API_KEY"])
        self._client = genai.GenerativeModel(model, system_instruction=system)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def generate(self, prompt: str) -> str:
        resp = self._client.generate_content(
            prompt,
            generation_config={"max_output_tokens": self.max_tokens, "temperature": self.temperature},
        )
        return resp.text
