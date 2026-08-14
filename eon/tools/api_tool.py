"""
eon.tools.api_tool
====================
APITool — llamadas HTTP arbitrarias con auth.

Dependencia opcional: httpx
"""
from __future__ import annotations

import logging
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.api")


class APITool(Tool):
    """Tool para llamadas API HTTP arbitrarias con autenticación.

    Soporta auth por header, Bearer token, o API key en query params.
    """

    name = "api"

    def __init__(
        self,
        default_headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._default_headers = default_headers or {}
        self._timeout = timeout
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import httpx
            except ImportError as exc:
                raise ImportError(
                    "httpx no está instalado. Instala con: pip install httpx"
                ) from exc
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    async def execute(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth_token: str | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Ejecuta una llamada API.

        Args:
            url: URL del endpoint.
            method: Método HTTP.
            headers: Headers adicionales.
            params: Query params.
            json_body: Body JSON.
            auth_token: Bearer token para Authorization header.
        """
        try:
            client = self._ensure_client()
            all_headers = dict(self._default_headers)
            if headers:
                all_headers.update(headers)
            if auth_token:
                all_headers["Authorization"] = f"Bearer {auth_token}"

            response = client.request(
                method,
                url,
                headers=all_headers,
                params=params,
                json=json_body,
                **kwargs,
            )

            try:
                data = response.json()
            except Exception:
                data = response.text[:5000]

            return ToolResult(
                ok=response.status_code < 400,
                data={
                    "status_code": response.status_code,
                    "data": data,
                    "headers": dict(response.headers),
                },
                error=None if response.status_code < 400 else f"HTTP {response.status_code}",
            )
        except ImportError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error en llamada API: {exc}")

    def close(self) -> None:
        """Cierra el cliente HTTP si fue creado."""
        if self._client is not None:
            self._client.close()
            self._client = None
