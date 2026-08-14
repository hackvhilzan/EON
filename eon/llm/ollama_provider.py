"""
eon.llm.ollama_provider
==========================
Implementación de LLM sobre Ollama (https://ollama.com), corriendo modelos
open-source (Llama, Qwen, Mistral, etc.) en local. Sin API key, sin límite
de tokens/créditos: el único límite real es el hardware donde corre Ollama.

Requiere tener Ollama instalado y corriendo (`ollama serve`, por defecto en
`http://localhost:11434`) y el modelo ya descargado (`ollama pull <modelo>`).
No requiere ningún SDK propietario -- se habla HTTP directo con `httpx`
(ya es dependencia de EON, ver `eon/requirements.txt`), así que no hace
falta instalar nada nuevo para usar este proveedor.
"""

import os

from .base import LLM

DEFAULT_MODEL = "llama3.1"
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaLLM(LLM):
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        system: str | None = None,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        timeout: float = 120.0,
    ):
        import httpx  # import perezoso, mismo criterio que el resto de proveedores

        self._base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._client = httpx.Client(timeout=timeout)
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.temperature = temperature

    def generate(self, prompt: str) -> str:
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": prompt})

        resp = self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
