"""
eon.llm.base
==============
Interfaz mínima que deben implementar todos los proveedores de IA (Claude, GPT, Gemini)
usados por EON. El resto del sistema programa contra esta clase, nunca contra un SDK
concreto, para poder cambiar de modelo sin tocar el resto del código.
"""

from abc import ABC, abstractmethod


class LLM(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Envía un prompt al modelo y devuelve el texto de la respuesta."""
        ...
