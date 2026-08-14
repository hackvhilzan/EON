"""
eon.llm.fallback_provider
============================
Resuelve el problema real de tener varios proveedores de LLM: un
proveedor de pago puede rechazar la llamada por límite de tasa, créditos
agotados (real o reportado por error) o estar simplemente caído; uno
gratis puede tener un límite diario más bajo. `FallbackLLM` envuelve una
cadena ORDENADA de proveedores y, ante cualquier fallo del actual, prueba
el siguiente -- sin que quien llama a `.generate()` tenga que enterarse
ni intervenir.

Deliberadamente NO intenta distinguir "es un rate-limit" de "es un error
de programación en el prompt": cualquier excepción del proveedor actual
hace pasar al siguiente. Es una decisión consciente -- distinguir tipos
de error por proveedor exigiría acoplarse a las excepciones concretas de
cada SDK (openai.RateLimitError, httpx.HTTPStatusError, etc.), justo lo
que la interfaz `LLM` (`base.py`) existe para evitar. El costo es que un
prompt realmente inválido se reintenta contra cada proveedor de la
cadena en vez de fallar rápido -- aceptable frente al beneficio de nunca
cortar el trabajo por un límite de un proveedor concreto.
"""

from __future__ import annotations

import os

from .base import LLM

DEFAULT_CHAIN = ["ollama", "groq", "openrouter", "claude"]


class AllProvidersFailedError(RuntimeError):
    """Ningún proveedor de la cadena pudo generar una respuesta. Trae el
    error de cada uno (`errores`, dict nombre -> excepción) para que quien
    lo capture pueda diagnosticar cuál fue el motivo real de cada fallo,
    en vez de perder esa información detrás de un mensaje genérico."""

    def __init__(self, errores: dict[str, Exception]):
        self.errores = errores
        detalle = "; ".join(f"{nombre}: {error}" for nombre, error in errores.items())
        super().__init__(f"Todos los proveedores de la cadena fallaron -- {detalle}")


class FallbackLLM(LLM):
    name = "fallback"

    def __init__(self, chain: list[str] | None = None, provider_kwargs: dict[str, dict] | None = None):
        """`chain`: orden de proveedores a probar (nombres de
        `provider.available_providers()`). Si se omite, usa
        `EON_LLM_FALLBACK_CHAIN` (coma-separado, ej.
        "ollama,groq,openrouter,claude") o `DEFAULT_CHAIN` si tampoco está
        seteada -- mismo criterio que `EON_LLM_PROVIDER` en `provider.py`.

        `provider_kwargs`: kwargs específicos por proveedor (ej.
        {"groq": {"model": "llama-3.1-8b-instant"}}), para no forzar a
        todos los proveedores de la cadena a compartir el mismo `model`.

        Los proveedores se construyen de forma perezosa (uno por uno,
        solo cuando le toca el turno en `.generate()`, nunca todos de
        antemano en `__init__`) -- así una API key ausente para un
        proveedor que ni siquiera hace falta usar (porque el primero de
        la cadena ya respondió) no revienta la construcción de
        `FallbackLLM` entero.
        """
        env_chain = os.environ.get("EON_LLM_FALLBACK_CHAIN")
        self._chain = chain or (env_chain.split(",") if env_chain else list(DEFAULT_CHAIN))
        self._chain = [nombre.strip() for nombre in self._chain if nombre.strip()]
        self._provider_kwargs = provider_kwargs or {}
        self._instancias: dict[str, LLM] = {}

    def _obtener(self, nombre: str) -> LLM:
        if nombre not in self._instancias:
            # Import perezoso: `provider.get_provider` ya sabe resolver
            # cualquier nombre conocido (incluidos los nuevos de esta
            # extensión) sin que este módulo tenga que importar cada
            # *_provider.py directamente.
            from . import provider

            self._instancias[nombre] = provider.get_provider(nombre, **self._provider_kwargs.get(nombre, {}))
        return self._instancias[nombre]

    def generate(self, prompt: str) -> str:
        errores: dict[str, Exception] = {}
        for nombre in self._chain:
            try:
                llm = self._obtener(nombre)
                return llm.generate(prompt)
            except Exception as error:  # noqa: BLE001 -- ver docstring del módulo
                errores[nombre] = error
                # Una construcción fallida (ej. API key ausente) no debe
                # dejar una entrada rota cacheada en `_instancias` que se
                # reintente sin sentido en la próxima llamada.
                self._instancias.pop(nombre, None)
                continue
        raise AllProvidersFailedError(errores)
