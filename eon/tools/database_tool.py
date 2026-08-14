"""
eon.tools.database_tool
=========================
DatabaseTool — consultas SQL read-only.

Dependencia opcional: sqlite3 (stdlib, siempre disponible)
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.database")


class DatabaseTool(Tool):
    """Tool para consultas SQL read-only.

    Solo permite SELECT — bloquea INSERT, UPDATE, DELETE, DROP, etc.
    Soporta SQLite por defecto. Para otros motores, se puede extender.
    """

    name = "database"

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path

    async def execute(
        self,
        query: str,
        params: tuple | None = None,
        db_path: str | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Ejecuta una consulta SQL read-only.

        Args:
            query: Consulta SQL (solo SELECT permitido).
            params: Parámetros para la consulta parametrizada.
            db_path: Ruta a la BD SQLite (override del default).
        """
        # Validar read-only
        query_upper = query.strip().upper()
        forbidden = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "ATTACH", "DETACH")
        for word in forbidden:
            if query_upper.startswith(word) or f" {word} " in query_upper:
                return ToolResult(
                    ok=False,
                    error=f"Operación {word} no permitida. DatabaseTool es read-only.",
                )

        path = db_path or self._db_path
        conn = None
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params or ())
            rows = [dict(r) for r in cursor.fetchall()]

            return ToolResult(
                ok=True,
                data={
                    "rows": rows,
                    "row_count": len(rows),
                    "query": query,
                },
            )
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error en consulta SQL: {exc}")
        finally:
            if conn is not None:
                conn.close()
