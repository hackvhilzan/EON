"""Excepciones del sandbox de EON."""
from __future__ import annotations


class SandboxError(Exception):
    """Error base del sandbox."""


class SandboxTimeout(SandboxError):
    """La ejecución superó el timeout configurado."""


class SandboxResourceExceeded(SandboxError):
    """La ejecución superó los límites de CPU o memoria."""


class SandboxViolation(SandboxError):
    """La ejecución intentó una operación no permitida (path traversal, etc.)."""
