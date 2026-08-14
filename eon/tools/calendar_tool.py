"""
eon.tools.calendar_tool
=========================
CalendarTool — crear/leer eventos (Google Calendar API).

Dependencia opcional: google-api-python-client + google-auth-oauthlib
Requiere OAuth flow completo.
"""

from __future__ import annotations

import logging
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.calendar")


class CalendarTool(Tool):
    """Tool para gestión de eventos de calendario.

    Soporta Google Calendar API. Requiere OAuth2.

    Configuración:
    - Credentials JSON en EON_GOOGLE_CREDENTIALS_PATH
    - Token en EON_GOOGLE_TOKEN_PATH
    """

    name = "calendar"

    def __init__(self, credentials_path: str | None = None) -> None:
        self._credentials_path = credentials_path

    def _ensure_service(self) -> Any:
        """Crea el servicio de Google Calendar. Lanza si no está configurado."""
        import os

        creds_path = self._credentials_path or os.environ.get("EON_GOOGLE_CREDENTIALS_PATH")
        if not creds_path:
            raise ImportError(
                "Google Calendar no configurado. Set EON_GOOGLE_CREDENTIALS_PATH "
                "con la ruta al archivo de credenciales OAuth2 JSON."
            )

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise ImportError(
                "google-api-python-client y google-auth-oauthlib no instalados. "
                "Instala con: pip install google-api-python-client google-auth-oauthlib"
            ) from exc

        creds = Credentials.from_authorized_user_file(creds_path)
        return build("calendar", "v3", credentials=creds)

    async def execute(
        self,
        action: str = "list",
        calendar_id: str = "primary",
        summary: str = "",
        start_time: str = "",
        end_time: str = "",
        description: str = "",
        max_results: int = 10,
        **kwargs: Any,
    ) -> ToolResult:
        """Gestiona eventos de calendario.

        Args:
            action: "list", "create", o "delete".
            calendar_id: ID del calendario.
            summary: Título del evento (para create).
            start_time: ISO datetime de inicio.
            end_time: ISO datetime de fin.
            description: Descripción del evento.
            max_results: Máximo número de eventos (para list).
        """
        try:
            service = self._ensure_service()

            if action == "list":
                events_result = (
                    service.events()
                    .list(
                        calendarId=calendar_id,
                        maxResults=max_results,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
                events = events_result.get("items", [])
                return ToolResult(
                    ok=True,
                    data={"events": events, "count": len(events)},
                )

            elif action == "create":
                event = {
                    "summary": summary,
                    "description": description,
                    "start": {"dateTime": start_time},
                    "end": {"dateTime": end_time},
                }
                created = service.events().insert(calendarId=calendar_id, body=event).execute()
                return ToolResult(
                    ok=True,
                    data={"event_id": created.get("id"), "html_link": created.get("htmlLink")},
                )

            else:
                return ToolResult(ok=False, error=f"Acción desconocida: {action}")

        except ImportError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error en Calendar API: {exc}")
