"""
eon.tools.terminal_tool
========================
TerminalTool — ejecución de comandos shell (bajo sandbox).

ADVERTENCIA: Esta Tool es de ALTO RIESGO. Debe usarse solo bajo
gobernanza del PolicyEngine y con SandboxProfile restrictivo.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.terminal")


class TerminalTool(Tool):
    """Tool de ejecución de comandos shell.

    Ejecuta comandos en un subprocess aislado con timeout y
    captura de stdout/stderr. Debe estar gobernada por SandboxProfile.
    """

    name = "terminal"

    def __init__(self, default_timeout: float = 30.0) -> None:
        self._default_timeout = default_timeout

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Ejecuta un comando shell.

        Args:
            command: Comando a ejecutar.
            cwd: Directorio de trabajo.
            timeout: Timeout en segundos.
            env: Variables de entorno.
        """
        import time

        actual_timeout = timeout or self._default_timeout
        start = time.monotonic()

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=actual_timeout,
                env=env,
            )
            elapsed = time.monotonic() - start

            return ToolResult(
                ok=result.returncode == 0,
                data={
                    "stdout": result.stdout[:10000],
                    "stderr": result.stderr[:5000],
                    "returncode": result.returncode,
                },
                error=None if result.returncode == 0 else f"Exit code {result.returncode}",
                duration_seconds=elapsed,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return ToolResult(
                ok=False,
                error=f"Timeout después de {actual_timeout}s",
                duration_seconds=elapsed,
            )
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error ejecutando comando: {exc}")
