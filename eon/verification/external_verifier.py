"""
eon.verification.external_verifier
====================================
Layer 5: ExternalVerifier.

Hooks para validadores externos: linters, type checkers, compiladores.
Configurable por capability.
Desactivado por defecto — requiere comandos explícitos.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from .models import LayerResult, VerificationStatus

logger = logging.getLogger("eon.verification.external")


class ExternalVerifier:
    """Verifica resultados usando validadores externos.

    Configurable: cada capability puede registrar un comando
    de validación externo (ej. mypy, ruff, html-validator).

    Desactivado por defecto — no ejecuta nada si no hay
    comandos registrados.
    """

    name = "external"

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        # Mapeo: capability_id → comando (lista de args)
        self._commands: dict[str, list[str]] = {}

    def register_command(self, capability_id: str, command: list[str]) -> None:
        """Registra un comando de validación para una capability."""
        self._commands[capability_id] = command

    def verificar(
        self,
        objective: Any,
        evidence: dict[str, Any],
        artifacts: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LayerResult:
        capability_id = kwargs.get("capability_id", "")

        if not self._commands:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay comandos externos registrados",
            )

        if capability_id and capability_id not in self._commands:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo=f"Sin comando externo para capability {capability_id}",
            )

        if not artifacts:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay artefactos para validar",
            )

        commands_to_run = []
        if capability_id and capability_id in self._commands:
            commands_to_run.append(self._commands[capability_id])
        else:
            commands_to_run.extend(self._commands.values())

        results = []
        for cmd in commands_to_run:
            for artifact_name, artifact_content in artifacts.items():
                content_str = str(artifact_content)
                ok, output = self._run_external(cmd, content_str)
                results.append((cmd[0], artifact_name, ok, output))

        if not results:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No se ejecutó ningún validador",
            )

        all_passed = all(r[2] for r in results)
        confidence = sum(1 for r in results if r[2]) / len(results)

        return LayerResult(
            layer_name=self.name,
            status=VerificationStatus.PASSED if all_passed else VerificationStatus.FAILED,
            confidence=confidence,
            motivo="; ".join(f"{r[0]}({r[1]}): {'OK' if r[2] else r[3][:80]}" for r in results),
            details={
                "results": [{"tool": r[0], "artifact": r[1], "passed": r[2], "output": r[3][:200]} for r in results]
            },
        )

    def _run_external(self, command: list[str], content: str) -> tuple[bool, str]:
        """Ejecuta un comando externo contra el contenido del artefacto."""
        try:
            result = subprocess.run(
                command,
                input=content,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            if result.returncode == 0:
                return True, result.stdout
            return False, result.stderr[:500]
        except subprocess.TimeoutExpired:
            return False, f"Timeout después de {self._timeout}s"
        except FileNotFoundError:
            return False, f"Comando no encontrado: {command[0]}"
        except Exception as exc:
            return False, str(exc)
