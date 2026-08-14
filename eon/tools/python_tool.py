"""
eon.tools.python_tool
=======================
PythonTool — ejecución de código Python (bajo sandbox).

ADVERTENCIA: Esta Tool es de ALTO RIESGO. Debe usarse solo bajo
gobernanza del PolicyEngine y con SandboxProfile restrictivo.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.python")


class PythonTool(Tool):
    """Tool de ejecución de código Python.

    Ejecuta código Python en un subprocess aislado usando exec().
    Captura stdout y el resultado de la última expresión.
    """

    name = "python"

    def __init__(self, default_timeout: float = 30.0) -> None:
        self._default_timeout = default_timeout

    async def execute(
        self,
        code: str,
        timeout: float | None = None,
        globals_dict: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Ejecuta código Python.

        Args:
            code: Código Python a ejecutar.
            timeout: Timeout en segundos.
            globals_dict: Variables globales inyectadas.
        """
        import subprocess
        import sys
        import time

        actual_timeout = timeout or self._default_timeout
        start = time.monotonic()

        # Serializar globals y code de forma segura con json.dumps
        # para evitar inyección y problemas de escaping
        globals_json = json.dumps(globals_dict or {})
        code_json = json.dumps(code)

        # Wrapper como string plano (no f-string) para evitar conflictos
        # con llaves de Python. Los valores se inyectan via json.loads.
        # Usamos %r (repr) para generar string literals Python válidos --
        # .format() exigiría escapar cada '{' literal de los json.dumps
        # de abajo, más frágil que el %-format aquí.
        wrapper = (  # noqa: UP031
            "import sys, json\n"
            "_g = dict(json.loads(%r))\n"
            "_code = json.loads(%r)\n"
            "try:\n"
            "    exec(compile(_code, '<python_tool>', 'exec'), _g)\n"
            "    _result = _g.get('_result', None)\n"
            "    print(json.dumps({'ok': True, 'result': str(_result)}))\n"
            "except Exception as e:\n"
            "    print(json.dumps({'ok': False, 'error': str(e)}))\n"
            "    sys.exit(1)\n"
        ) % (globals_json, code_json)

        try:
            result = subprocess.run(
                [sys.executable, "-c", wrapper],
                capture_output=True,
                text=True,
                timeout=actual_timeout,
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
            return ToolResult(ok=False, error=f"Error ejecutando Python: {exc}")
