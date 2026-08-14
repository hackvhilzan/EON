"""
eon.tools.internet_tool
=========================
InternetTool — búsqueda web con httpx, resultados parseados.

Dependencia opcional: httpx
"""

from __future__ import annotations

import logging
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.internet")


class InternetTool(Tool):
    """Tool de búsqueda web.

    Realiza peticiones HTTP GET y devuelve el contenido parseado.
    Requiere httpx instalado.
    """

    name = "internet"

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import httpx
            except ImportError as exc:
                raise ImportError("httpx no está instalado. Instala con: pip install httpx") from exc
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    async def execute(self, url: str, method: str = "GET", **kwargs: Any) -> ToolResult:
        """Realiza una petición HTTP.

        Args:
            url: URL a la que hacer la petición.
            method: Método HTTP (GET, POST, etc.).
            **kwargs: Headers, params, json, etc.
        """
        try:
            client = self._ensure_client()
            response = client.request(method, url, **kwargs)
            return ToolResult(
                ok=response.status_code < 400,
                data={
                    "status_code": response.status_code,
                    "text": response.text[:10000],
                    "headers": dict(response.headers),
                    "url": str(response.url),
                },
                error=None if response.status_code < 400 else f"HTTP {response.status_code}",
                duration_seconds=response.elapsed.total_seconds() if response.elapsed else 0.0,
            )
        except ImportError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error en petición HTTP: {exc}")

    def close(self) -> None:
        """Cierra el cliente HTTP si fue creado."""
        if self._client is not None:
            self._client.close()
            self._client = None
