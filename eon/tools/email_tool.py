"""
eon.tools.email_tool
======================
EmailTool — envío de emails via SMTP.

Dependencia: smtplib (stdlib, siempre disponible)
Requiere configuración SMTP.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.email")


class EmailTool(Tool):
    """Tool para envío de emails via SMTP.

    Configuración via variables de entorno o constructor:
    - EON_SMTP_HOST: servidor SMTP
    - EON_SMTP_PORT: puerto (default 587)
    - EON_SMTP_USER: usuario
    - EON_SMTP_PASSWORD: contraseña
    - EON_SMTP_FROM: email remitente
    """

    name = "email"

    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        from_email: str | None = None,
    ) -> None:
        self._host = smtp_host or os.environ.get("EON_SMTP_HOST")
        self._port = smtp_port or int(os.environ.get("EON_SMTP_PORT", "587"))
        self._user = smtp_user or os.environ.get("EON_SMTP_USER")
        self._password = smtp_password or os.environ.get("EON_SMTP_PASSWORD")
        self._from = from_email or os.environ.get("EON_SMTP_FROM", self._user)

    async def execute(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        """Envía un email.

        Args:
            to: Email destinatario.
            subject: Asunto.
            body: Cuerpo del mensaje.
            html: Si True, body es HTML.
        """
        if not self._host:
            return ToolResult(
                ok=False,
                error="SMTP no configurado. Set EON_SMTP_HOST o pasa smtp_host al constructor.",
            )

        try:
            msg = MIMEMultipart()
            msg["From"] = self._from or self._user or ""
            msg["To"] = to
            msg["Subject"] = subject

            msg.attach(MIMEText(body, "html" if html else "plain"))

            with smtplib.SMTP(self._host, self._port) as server:
                if self._port == 587:
                    server.starttls()
                if self._user and self._password:
                    server.login(self._user, self._password)
                server.sendmail(self._from or "", to, msg.as_string())

            return ToolResult(
                ok=True,
                data={"to": to, "subject": subject, "from": self._from},
            )
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error enviando email: {exc}")
