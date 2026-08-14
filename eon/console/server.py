"""
eon.console.server
====================
Servidor HTTP mínimo usando `http.server` de la stdlib.

Sin dependencias externas (sin FastAPI, sin Flask): suficiente para
el MVP de la consola local.
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..runtime import KernelRuntime

logger = logging.getLogger("eon.console")


class _ConsoleHandler(BaseHTTPRequestHandler):
    """Handler HTTP para la consola de EON."""

    def _send_json(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.strip("/")

        if path == "estado":
            self._send_json(200, {"status": "ok"})
            return

        if path == "ejecuciones":
            runtime: KernelRuntime = self.server.runtime  # type: ignore[attr-defined]
            ejecuciones = runtime.coordinator.listar()
            self._send_json(
                200,
                {
                    "ejecuciones": [
                        {
                            "id": e.id,
                            "estado": e.estado.value if hasattr(e.estado, "value") else str(e.estado),
                            "objective_id": e.objective_id,
                            "plan_id": e.plan_id,
                            "package_id": e.package_id,
                        }
                        for e in ejecuciones
                    ]
                },
            )
            return

        if path.startswith("ejecuciones/"):
            runtime: KernelRuntime = self.server.runtime  # type: ignore[attr-defined]
            exec_id = path.split("/", 1)[1]
            try:
                execution = runtime.coordinator.obtener(exec_id)
                self._send_json(
                    200,
                    {
                        "id": execution.id,
                        "estado": execution.estado.value
                        if hasattr(execution.estado, "value")
                        else str(execution.estado),
                        "objective_id": execution.objective_id,
                        "plan_id": execution.plan_id,
                        "workspace_id": execution.workspace_id,
                        "package_id": execution.package_id,
                    },
                )
            except Exception:
                self._send_json(404, {"error": f"Ejecución no encontrada: {exec_id}"})
            return

        self._send_json(404, {"error": f"Ruta no encontrada: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.strip("/")

        if path == "ejecuciones":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "JSON inválido."})
                return

            descripcion = payload.get("descripcion")
            criterio = payload.get("criterio_de_exito")
            if not descripcion or not criterio:
                self._send_json(400, {"error": "Se requieren 'descripcion' y 'criterio_de_exito'."})
                return

            runtime: KernelRuntime = self.server.runtime  # type: ignore[attr-defined]
            try:
                result = runtime.run(descripcion, criterio)
                self._send_json(
                    201,
                    {
                        "execution_id": result.execution_id,
                        "package_id": result.package_id,
                        "package_state": result.package_state,
                    },
                )
            except Exception as exc:
                logger.exception("Error en ejecución")
                self._send_json(500, {"error": str(exc)})
            return

        self._send_json(404, {"error": f"Ruta no encontrada: {path}"})

    def log_message(self, format: str, *args: Any) -> None:
        logger.info(format, *args)


class ConsoleServer:
    """Servidor HTTP para la consola de EON."""

    def __init__(
        self,
        root: str | Path = ".eon_runtime",
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.root = Path(root).resolve()
        self.host = host
        self.port = port
        self.runtime = KernelRuntime(root=self.root)
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        """Inicia el servidor HTTP."""
        self._server = ThreadingHTTPServer((self.host, self.port), _ConsoleHandler)
        self._server.runtime = self.runtime  # type: ignore[attr-defined]
        logger.info("Consola EON escuchando en http://%s:%s", self.host, self.port)
        self._server.serve_forever()

    def stop(self) -> None:
        """Detiene el servidor HTTP."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
