"""
eon.console
===========
REST API minimalista para el Kernel EON usando solo ``http.server``
de la stdlib — sin Flask, sin FastAPI, sin dependencias externas.

Soporta autenticación mediante Bearer tokens (API keys) y TLS
(certificados auto-firmados o de CA).

Endpoints:
- GET  /health           — health check (no requiere auth)
- GET  /executions       — lista ejecuciones del coordinator
- GET  /executions/{id}  — detalle de una ejecución
- GET  /metrics          — snapshot de telemetría
- POST /run              — inicia una nueva ejecución (JSON body)

Uso básico::

    from eon.console import ConsoleServer
    from eon.runtime import KernelRuntime

    runtime = KernelRuntime(...)
    server = ConsoleServer(runtime, port=8080)
    server.start()   # non-blocking (daemon thread)

Uso con auth + TLS::

    server = ConsoleServer(
        runtime,
        port=8443,
        auth_token="my-secret-token",
        tls_certfile="/path/to/cert.pem",
        tls_keyfile="/path/to/key.pem",
    )
    server.start()  # HTTPS con Bearer token
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


class AuthProvider:
    """Proveedor de autenticación por Bearer token.

    Soporta:
    - Token único estático (``auth_token="..."``)
    - Múltiples tokens (``auth_tokens=["t1", "t2"]``)
    - Comparación en tiempo constante para prevenir timing attacks.

    ``/health`` nunca requiere auth. El resto de endpoints sí.
    """

    def __init__(
        self,
        auth_token: str | None = None,
        auth_tokens: list[str] | None = None,
    ) -> None:
        tokens: list[str] = []
        if auth_token:
            tokens.append(auth_token)
        if auth_tokens:
            tokens.extend(auth_tokens)
        self._tokens = tokens

    @property
    def enabled(self) -> bool:
        return len(self._tokens) > 0

    def validate(self, bearer_token: str | None) -> bool:
        """Valida un Bearer token en tiempo constante.

        Si no hay tokens configurados, todo pasa (auth deshabilitada).
        """
        if not self._tokens:
            return True
        if not bearer_token:
            return False
        return any(hmac.compare_digest(bearer_token, valid_token) for valid_token in self._tokens)

    def generate_token(self) -> str:
        """Genera un token aleatorio seguro (32 bytes hex)."""
        return secrets.token_hex(32)


class _ConsoleHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the EON console."""

    # Injected by ConsoleServer
    runtime: Any = None
    metrics: Any = None
    auth: AuthProvider | None = None

    # Endpoints que NO requieren autenticación
    PUBLIC_PATHS: frozenset = frozenset({"/health"})

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default logging."""
        pass

    def _check_auth(self, path: str) -> bool:
        """Verifica autenticación. Retorna True si pasa o no está habilitada."""
        if self.auth is None or not self.auth.enabled:
            return True
        if path in self.PUBLIC_PATHS:
            return True
        # Extraer Bearer token del header Authorization
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return self.auth.validate(token)
        return False

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_unauthorized(self) -> None:
        self._send_json(
            401,
            {
                "error": "Unauthorized",
                "detail": "Missing or invalid Bearer token. Use 'Authorization: Bearer <token>'.",
            },
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # Auth check (except public paths)
        if not self._check_auth(path):
            self._send_unauthorized()
            return

        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if path == "/executions":
            try:
                store = self.runtime._coordinator_store
                executions = []
                for execution in store.list():
                    executions.append(
                        {
                            "id": execution.id,
                            "estado": execution.estado.value
                            if hasattr(execution.estado, "value")
                            else str(execution.estado),
                            "objective_id": getattr(execution, "objective_id", ""),
                            "plan_id": getattr(execution, "plan_id", ""),
                            "workspace_id": getattr(execution, "workspace_id", ""),
                            "package_id": getattr(execution, "package_id", ""),
                        }
                    )
                self._send_json(200, {"executions": executions})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if path.startswith("/executions/"):
            exec_id = path[len("/executions/") :]
            try:
                execution = self.runtime._coordinator_store.get(exec_id)
                if execution is None:
                    self._send_json(404, {"error": f"Execution '{exec_id}' not found"})
                    return
                self._send_json(
                    200,
                    {
                        "id": execution.id,
                        "estado": execution.estado.value
                        if hasattr(execution.estado, "value")
                        else str(execution.estado),
                        "objective_id": getattr(execution, "objective_id", ""),
                        "plan_id": getattr(execution, "plan_id", ""),
                        "workspace_id": getattr(execution, "workspace_id", ""),
                        "package_id": getattr(execution, "package_id", ""),
                    },
                )
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/metrics":
            try:
                if self.metrics is not None:
                    self._send_json(200, self.metrics.snapshot())
                else:
                    self._send_json(200, {"error": "metrics not configured"})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self._send_json(404, {"error": f"Not found: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Auth check
        if not self._check_auth(path):
            self._send_unauthorized()
            return

        if path == "/run":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else b"{}"
                data = json.loads(body)

                descripcion = data.get("descripcion", "")
                criterio_de_exito = data.get("criterio_de_exito", "")
                if not descripcion or not criterio_de_exito:
                    self._send_json(400, {"error": "descripcion and criterio_de_exito are required"})
                    return

                result = self.runtime.run(
                    descripcion=descripcion,
                    criterio_de_exito=criterio_de_exito,
                    configuracion=data.get("configuracion"),
                    execution_id=data.get("execution_id"),
                )
                self._send_json(
                    201,
                    {
                        "execution_id": result.execution_id,
                        "package_id": result.package_id,
                        "package_state": result.package_state,
                    },
                )
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self._send_json(404, {"error": f"Not found: {path}"})


class TLSCertGenerator:
    """Genera certificados auto-firmados para TLS usando solo stdlib.

    Usa ``ssl`` de Python. Si ``cryptography`` no está instalado,
    genera un certificado usando ``openssl`` via subprocess (común en Linux).
    Si tampoco está disponible, ``ConsoleServer`` cae a HTTP plano.

    Para producción: usa certificados de una CA real (Let's Encrypt, etc.).
    """

    @staticmethod
    def generate_self_signed(
        certfile: str,
        keyfile: str,
        common_name: str = "localhost",
        days: int = 365,
    ) -> bool:
        """Genera un certificado auto-firmado.

        Returns:
            True si se generó, False si no fue posible.
        """
        # Intentar con openssl (común en Linux/macOS)
        import subprocess

        try:
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    keyfile,
                    "-out",
                    certfile,
                    "-days",
                    str(days),
                    "-nodes",
                    "-subj",
                    f"/CN={common_name}",
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

        # Intentar con cryptography si está instalado
        try:
            import datetime

            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                ]
            )
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.utcnow())
                .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=days))
                .sign(key, hashes.SHA256())
            )
            with open(certfile, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            with open(keyfile, "wb") as f:
                f.write(
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.TraditionalOpenSSL,
                        serialization.NoEncryption(),
                    )
                )
            return True
        except ImportError:
            return False


class ConsoleServer:
    """HTTP/HTTPS server for the EON console.

    Wraps ``ThreadingHTTPServer`` with the runtime and optional metrics.
    Starts in a daemon thread — non-blocking.

    Args:
        runtime: A ``KernelRuntime`` or ``EnhancedKernelRuntime`` instance.
        port: TCP port to listen on.
        host: Bind address (default 127.0.0.1).
        metrics: Optional ``MetricsRecorder`` instance.
        auth_token: Bearer token for API authentication. If None, no auth.
        auth_tokens: List of valid Bearer tokens (alternative to auth_token).
        tls_certfile: Path to TLS certificate file (PEM). Enables HTTPS.
        tls_keyfile: Path to TLS private key file (PEM).
        tls_auto_generate: If True and tls_certfile/keyfile given but files
            don't exist, auto-generate self-signed certificates.

    Security notes:
        - ``/health`` is always public (no auth required).
        - All other endpoints require ``Authorization: Bearer <token>``.
        - Token comparison uses ``hmac.compare_digest`` (timing-safe).
        - TLS is optional. Without it, tokens travel in plaintext
          (only safe on localhost or behind a reverse proxy).
    """

    def __init__(
        self,
        runtime: Any,
        port: int = 8080,
        host: str = "127.0.0.1",
        metrics: Any = None,
        auth_token: str | None = None,
        auth_tokens: list[str] | None = None,
        tls_certfile: str | None = None,
        tls_keyfile: str | None = None,
        tls_auto_generate: bool = False,
    ) -> None:
        self._runtime = runtime
        self._port = port
        self._host = host
        self._metrics = metrics
        self._auth = AuthProvider(auth_token=auth_token, auth_tokens=auth_tokens)
        self._tls_certfile = tls_certfile
        self._tls_keyfile = tls_keyfile
        self._tls_auto_generate = tls_auto_generate
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _make_handler(self) -> type[_ConsoleHandler]:
        """Create a handler class with runtime, metrics and auth injected."""
        runtime = self._runtime
        metrics = self._metrics
        auth = self._auth

        class _Handler(_ConsoleHandler):
            pass

        _Handler.runtime = runtime
        _Handler.metrics = metrics
        _Handler.auth = auth
        return _Handler

    def _setup_tls(self) -> ssl.SSLContext | None:
        """Set up TLS context if cert files are available.

        Returns SSLContext or None (HTTP fallback).
        """
        if not self._tls_certfile or not self._tls_keyfile:
            return None

        # Auto-generate self-signed certs if requested and files don't exist
        if self._tls_auto_generate and not (os.path.exists(self._tls_certfile) and os.path.exists(self._tls_keyfile)):
            TLSCertGenerator.generate_self_signed(
                certfile=self._tls_certfile,
                keyfile=self._tls_keyfile,
                common_name=self._host,
            )

        if not os.path.exists(self._tls_certfile) or not os.path.exists(self._tls_keyfile):
            return None  # Files don't exist, fall back to HTTP

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(
            certfile=self._tls_certfile,
            keyfile=self._tls_keyfile,
        )
        return ctx

    def start(self) -> None:
        """Start the server in a daemon thread."""
        if self._server is not None:
            return  # already running

        self._server = ThreadingHTTPServer((self._host, self._port), self._make_handler())

        # Wrap socket with TLS if configured
        tls_ctx = self._setup_tls()
        if tls_ctx is not None:
            self._server.socket = tls_ctx.wrap_socket(self._server.socket, server_side=True)

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the server."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def is_tls(self) -> bool:
        """True if the server is configured for TLS (HTTPS)."""
        return self._setup_tls() is not None

    @property
    def is_auth_enabled(self) -> bool:
        """True if Bearer token authentication is enabled."""
        return self._auth.enabled

    @property
    def url(self) -> str:
        scheme = "https" if self.is_tls else "http"
        return f"{scheme}://{self._host}:{self._port}"

    def generate_token(self) -> str:
        """Generate a random secure API token (32 bytes hex)."""
        return self._auth.generate_token()
