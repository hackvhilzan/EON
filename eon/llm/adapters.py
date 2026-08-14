"""
eon.llm.adapters
================
Adapters that bridge the kernel's LLM provider (``eon.llm.base.LLM``)
with the protocols expected by the new Fase 7-9 modules.

- ``LLMJudgeAdapter``: adapts ``LLM`` to ``verification.llm_judge_verifier.JudgeProtocol``.
- ``DecomposerLLMAdapter``: adapts ``LLM`` for ``planning.decomposer.ObjectiveDecomposer``.

Both are thin wrappers: they format the prompt, call ``LLM.generate()``,
and parse the response. No external API calls in tests — always use a
``FakeLLM`` or stub.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import LLM


class LLMJudgeAdapter:
    """Adapts an ``LLM`` provider to the ``JudgeProtocol`` expected by
    ``LLMJudgeVerifier``.

    ``JudgeProtocol.juzgar(criterio, resultado) -> (cumple, confianza, justificacion)``

    The adapter builds a prompt asking the LLM to evaluate the result
    against the criteria and respond in JSON format.
    """

    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    def juzgar(self, criterio: str, resultado: Any) -> tuple[bool, float, str]:
        """Judge whether ``resultado`` meets ``criterio``.

        Returns ``(cumple, confianza, justificacion)``.
        """
        result_str = str(resultado) if not isinstance(resultado, str) else resultado
        prompt = (
            "Eres un juez de verificación. Evalúa si el resultado cumple el criterio.\n\n"
            f"Criterio de éxito: {criterio}\n"
            f"Resultado: {result_str[:5000]}\n\n"
            "Responde ÚNICAMENTE en formato JSON:\n"
            '{"cumple": true/false, "confianza": 0.0-1.0, "justificacion": "..."}'
        )

        try:
            response = self._llm.generate(prompt)
            return self._parse_judge_response(response)
        except Exception as exc:
            return False, 0.0, f"Error en LLM judge: {exc}"

    @staticmethod
    def _parse_judge_response(response: str) -> tuple[bool, float, str]:
        """Parse the LLM's JSON response into (cumple, confianza, justificacion)."""
        # Try JSON first
        try:
            data = json.loads(response)
            cumple = bool(data.get("cumple", False))
            confianza = float(data.get("confianza", 0.0))
            confianza = max(0.0, min(1.0, confianza))
            justificacion = str(data.get("justificacion", ""))
            return cumple, confianza, justificacion
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: regex extraction
        cumple_match = re.search(r'"cumple"\s*:\s*(true|false)', response, re.IGNORECASE)
        conf_match = re.search(r'"confianza"\s*:\s*([\d.]+)', response)
        just_match = re.search(r'"justificacion"\s*:\s*"([^"]*)"', response)

        cumple = cumple_match and cumple_match.group(1).lower() == "true"
        confianza = float(conf_match.group(1)) if conf_match else 0.5
        confianza = max(0.0, min(1.0, confianza))
        justificacion = just_match.group(1) if just_match else response[:200]

        return bool(cumple), confianza, justificacion


class DecomposerLLMAdapter:
    """Adapts an ``LLM`` provider for use by ``ObjectiveDecomposer``.

    The decomposer uses this adapter to get LLM-based sub-objective
    suggestions when heuristic decomposition is insufficient.

    The adapter asks the LLM to decompose an objective into sub-objectives
    and returns them as a list of strings.
    """

    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    def decompose(self, objective: str, max_sub: int = 5) -> list[str]:
        """Ask the LLM to decompose ``objective`` into sub-objectives.

        Returns a list of sub-objective descriptions (max ``max_sub``).
        """
        prompt = (
            "Descompone el siguiente objetivo en sub-objectivos más simples.\n"
            f"Objetivo: {objective}\n"
            f"Máximo {max_sub} sub-objectivos.\n\n"
            "Responde ÚNICAMENTE con una lista JSON de strings:\n"
            '["sub-objetivo 1", "sub-objetivo 2", ...]'
        )

        try:
            response = self._llm.generate(prompt)
            return self._parse_decompose_response(response, max_sub)
        except Exception:
            return []

    @staticmethod
    def _parse_decompose_response(response: str, max_sub: int) -> list[str]:
        """Parse the LLM's JSON list response."""
        try:
            data = json.loads(response)
            if isinstance(data, list):
                return [str(item) for item in data[:max_sub]]
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: line-by-line extraction
        lines = [line.strip().lstrip("0123456789.-) ") for line in response.split("\n")]
        return [line for line in lines if line and len(line) > 3][:max_sub]
