"""
eon.sandbox
=============
Sandbox real para ejecución aislada de código no confiable.

Niveles de aislamiento:
- Timeout: garantizado via subprocess + process group kill
- CPU/Memoria: garantizado via resource.setrlimit en preexec_fn
- Filesystem: temp dir aislado, path traversal bloqueado, allowlist
- Env: variables limpias, secrets del host no heredados
- Network: degradable — socket blocking para Python, best-effort para otros
"""

from __future__ import annotations

# Re-exportado aquí (vive físicamente en eon.governance.models, que ya
# gobierna las políticas del sandbox) para que un caller que solo
# necesita configurar el sandbox pueda depender de eon.sandbox y nada
# más -- ver eon/codeforces/ (regla verificada en
# eon/tests/test_codeforces_boundary.py).
from ..governance.models import SandboxProfile
from .exceptions import (
    SandboxError,
    SandboxResourceExceeded,
    SandboxTimeout,
    SandboxViolation,
)
from .executor import SandboxExecutor, SandboxResult

__all__ = [
    "SandboxExecutor",
    "SandboxProfile",
    "SandboxResult",
    "SandboxError",
    "SandboxTimeout",
    "SandboxResourceExceeded",
    "SandboxViolation",
]
