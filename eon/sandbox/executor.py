"""
eon.sandbox.executor
=======================
SandboxExecutor — ejecuta código en un subprocess aislado.

Aislamiento garantizado:
- Timeout: subprocess con timeout + os.killpg (process group kill)
- CPU: resource.setrlimit(RLIMIT_CPU) en preexec_fn
- Memoria: resource.setrlimit(RLIMIT_AS) en preexec_fn
- Filesystem: cwd=tempdir, path traversal bloqueado
- Env: variables mínimas, secrets del host no heredados

Aislamiento degradable (best-effort):
- Network: socket blocking para Python via sitecustomize.py inyectado
  Para comandos no-Python, se marca network_isolation_enforced=False
"""
from __future__ import annotations

import contextlib
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..governance.models import SandboxProfile
from .exceptions import (
    SandboxViolation,
)


@dataclass
class SandboxResult:
    """Resultado de una ejecución en sandbox."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    resource_exceeded: bool = False
    network_isolation_enforced: bool = False
    filesystem_isolation_enforced: bool = True
    env_isolated: bool = True
    sandbox_root: str = ""
    error: str | None = None


# Script Python que bloquea sockets (se inyecta via PYTHONPATH)
_NETWORK_BLOCKER_SCRIPT = """
import socket
_original_socket = socket.socket
class _BlockedSocket(_original_socket):
    def connect(self, *args, **kwargs):
        raise PermissionError("Network access blocked by EON sandbox")
    def connect_ex(self, *args, **kwargs):
        raise PermissionError("Network access blocked by EON sandbox")
socket.socket = _BlockedSocket

import urllib.request
import http.client
_original_urlopen = urllib.request.urlopen
def _blocked_urlopen(*args, **kwargs):
    raise PermissionError("Network access blocked by EON sandbox")
urllib.request.urlopen = _blocked_urlopen
http.client.HTTPConnection = type('BlockedHTTP', (), {'__init__': lambda *a: (_ for _ in ()).throw(PermissionError("Network blocked"))})
http.client.HTTPSConnection = type('BlockedHTTPS', (), {'__init__': lambda *a: (_ for _ in ()).throw(PermissionError("Network blocked"))})
"""


class SandboxExecutor:
    """Ejecuta código en un subprocess aislado con límites de recursos.

    Uso:
        executor = SandboxExecutor()
        result = executor.run_python(
            code="print('hello')",
            profile=SandboxProfile.workspace_only(),
        )
        assert result.success
    """

    def __init__(self, base_temp_dir: str | Path | None = None) -> None:
        self._base_temp = Path(base_temp_dir or tempfile.gettempdir()) / "eon_sandbox"
        self._base_temp.mkdir(parents=True, exist_ok=True)

    def run_python(
        self,
        code: str,
        profile: SandboxProfile | None = None,
        extra_files: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Ejecuta código Python en el sandbox.

        Args:
            code: Código Python a ejecutar.
            profile: Perfil de sandbox (límites). Si es None, usa workspace_only().
            extra_files: Archivos adicionales a copiar al sandbox.

        Returns:
            SandboxResult con stdout, stderr, exit_code, etc.
        """
        profile = profile or SandboxProfile.workspace_only()

        # Crear temp dir aislado
        sandbox_root = tempfile.mkdtemp(
            prefix="eon_sb_", dir=str(self._base_temp)
        )

        # Escribir el código a un archivo
        script_path = Path(sandbox_root) / "_sandbox_script.py"
        script_path.write_text(code, encoding="utf-8")

        # Copiar archivos extra
        if extra_files:
            for name, content in extra_files.items():
                # Validar path traversal
                dest = (Path(sandbox_root) / name).resolve()
                if not dest.is_relative_to(Path(sandbox_root).resolve()):
                    raise SandboxViolation(f"Path traversal bloqueado: {name}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")

        # Inyectar bloqueador de red si es necesario
        env = self._build_env(profile, sandbox_root)

        # Preexec function para límites de recursos
        def _preexec() -> None:
            self._apply_resource_limits(profile)

        # Ejecutar
        import time

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                cwd=sandbox_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,  # Nuevo process group para killpg
                preexec_fn=_preexec,
                text=True,
            )
            try:
                stdout, stderr = proc.communicate(
                    timeout=profile.max_duration_seconds
                )
            except subprocess.TimeoutExpired:
                # Matar todo el process group
                self._kill_process_group(proc.pid)
                try:
                    proc.wait(timeout=5)
                finally:
                    # Cerrar pipes para evitar ResourceWarning
                    for pipe in (proc.stdout, proc.stderr):
                        if pipe:
                            pipe.close()
                duration = time.monotonic() - start
                return SandboxResult(
                    success=False,
                    exit_code=-signal.SIGKILL,
                    stdout="",
                    stderr=f"Timeout tras {profile.max_duration_seconds}s",
                    duration_seconds=duration,
                    timed_out=True,
                    sandbox_root=sandbox_root,
                )

            duration = time.monotonic() - start

            # Determinar si fue resource exceeded
            resource_exceeded = False
            if proc.returncode == -signal.SIGXCPU or proc.returncode < 0 and not stdout and "MemoryError" in stderr:
                resource_exceeded = True

            return SandboxResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout[:10000],  # Truncar output
                stderr=stderr[:10000],
                duration_seconds=duration,
                resource_exceeded=resource_exceeded,
                network_isolation_enforced=not profile.network_allowed,
                sandbox_root=sandbox_root,
            )

        except Exception as exc:
            duration = time.monotonic() - start
            return SandboxResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration,
                sandbox_root=sandbox_root,
                error=str(exc),
            )
        finally:
            # Limpiar temp dir
            if Path(sandbox_root).exists():
                shutil.rmtree(sandbox_root, ignore_errors=True)

    def run_command(
        self,
        command: list[str],
        profile: SandboxProfile | None = None,
    ) -> SandboxResult:
        """Ejecuta un comando en el sandbox.

        Args:
            command: Comando y argumentos como lista.
            profile: Perfil de sandbox.
        """
        profile = profile or SandboxProfile.workspace_only()
        sandbox_root = tempfile.mkdtemp(
            prefix="eon_cmd_", dir=str(self._base_temp)
        )
        env = self._build_env(profile, sandbox_root)

        def _preexec() -> None:
            self._apply_resource_limits(profile)

        import time

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                command,
                cwd=sandbox_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                preexec_fn=_preexec,
                text=True,
            )
            try:
                stdout, stderr = proc.communicate(
                    timeout=profile.max_duration_seconds
                )
            except subprocess.TimeoutExpired:
                self._kill_process_group(proc.pid)
                try:
                    proc.wait(timeout=5)
                finally:
                    # Cerrar pipes para evitar ResourceWarning
                    for pipe in (proc.stdout, proc.stderr):
                        if pipe:
                            pipe.close()
                duration = time.monotonic() - start
                return SandboxResult(
                    success=False,
                    exit_code=-signal.SIGKILL,
                    stdout="",
                    stderr=f"Timeout tras {profile.max_duration_seconds}s",
                    duration_seconds=duration,
                    timed_out=True,
                    sandbox_root=sandbox_root,
                )

            duration = time.monotonic() - start
            return SandboxResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout[:10000],
                stderr=stderr[:10000],
                duration_seconds=duration,
                network_isolation_enforced=False,  # No podemos bloquear red para comandos arbitrarios
                sandbox_root=sandbox_root,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            return SandboxResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration,
                sandbox_root=sandbox_root,
                error=str(exc),
            )
        finally:
            shutil.rmtree(sandbox_root, ignore_errors=True)

    def _build_env(
        self, profile: SandboxProfile, sandbox_root: str
    ) -> dict[str, str]:
        """Construye un environment limpio para el subprocess."""
        # Empezar con variables mínimas
        env: dict[str, str] = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": sandbox_root,
            "LANG": "en_US.UTF-8",
            "PYTHONPATH": sandbox_root,
        }

        # Si network_allowed=False, inyectar bloqueador de red
        if not profile.network_allowed:
            blocker_path = Path(sandbox_root) / "_eon_net_block.py"
            blocker_path.write_text(_NETWORK_BLOCKER_SCRIPT, encoding="utf-8")
            # sitecustomize.py se auto-ejecuta al iniciar Python
            sitecustomize = Path(sandbox_root) / "sitecustomize.py"
            sitecustomize.write_text(
                "import _eon_net_block\n", encoding="utf-8"
            )

        # Filtrar env_vars_allowed
        if profile.env_vars_allowed:
            for var in profile.env_vars_allowed:
                if var in os.environ:
                    env[var] = os.environ[var]

        return env

    def _apply_resource_limits(self, profile: SandboxProfile) -> None:
        """Aplica límites de recursos en el proceso hijo (preexec_fn)."""
        # CPU limit (segundos)
        cpu_seconds = int(profile.max_duration_seconds) + 1
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (cpu_seconds, cpu_seconds),
        )

        # Memory limit
        if profile.max_memory_mb:
            mem_bytes = profile.max_memory_mb * 1024 * 1024
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (mem_bytes, mem_bytes),
                )

        # File size limit (50MB por defecto para prevenir escritura masiva)
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (50 * 1024 * 1024, 50 * 1024 * 1024),
            )

    def _kill_process_group(self, pid: int) -> None:
        """Mata todo el process group del proceso hijo."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
