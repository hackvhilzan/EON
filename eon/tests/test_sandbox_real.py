"""
Tests del sandbox real (Fase 2.5).

Criterios de aceptación:
- Timeout: script que duerme más del límite → timeout, proceso matado
- CPU limit: loop infinito → falla por límite
- Memory limit: script que asigna mucha memoria → falla
- Filesystem: escribir dentro del sandbox permitido; path traversal bloqueado
- Env: una variable secreta del host no aparece en el child
- Network denied: run_python con socket bloqueado
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eon.governance.models import SandboxProfile
from eon.sandbox import SandboxExecutor
from eon.sandbox.exceptions import SandboxViolation


@pytest.fixture
def executor(tmp_path: Path) -> SandboxExecutor:
    return SandboxExecutor(base_temp_dir=tmp_path)


# ─── Basic execution ──────────────────────────────────────


class TestSandboxBasic:
    def test_simple_python(self, executor: SandboxExecutor):
        result = executor.run_python("print('hello world')")
        assert result.success
        assert "hello world" in result.stdout

    def test_exit_code_nonzero(self, executor: SandboxExecutor):
        result = executor.run_python("import sys; sys.exit(1)")
        assert not result.success
        assert result.exit_code == 1

    def test_stderr_captured(self, executor: SandboxExecutor):
        result = executor.run_python("import sys; sys.stderr.write('error msg')")
        assert "error msg" in result.stderr

    def test_extra_files_copied(self, executor: SandboxExecutor):
        result = executor.run_python(
            code="print(open('data.txt').read())",
            extra_files={"data.txt": "test content"},
        )
        assert result.success
        assert "test content" in result.stdout

    def test_run_python_receives_stdin(self, executor: SandboxExecutor):
        result = executor.run_python(
            code="import sys; print(sys.stdin.read().strip().upper())",
            stdin="hola sandbox\n",
        )
        assert result.success
        assert "HOLA SANDBOX" in result.stdout

    def test_run_command_receives_stdin(self, executor: SandboxExecutor):
        result = executor.run_command(["cat"], stdin="hola comando\n")
        assert result.success
        assert "hola comando" in result.stdout


# ─── Timeout ─────────────────────────────────────────────


class TestSandboxTimeout:
    def test_timeout_kills_process(self, executor: SandboxExecutor):
        profile = SandboxProfile(
            profile_id="test-timeout",
            max_duration_seconds=1.0,
        )
        result = executor.run_python(
            code="import time; time.sleep(10)",
            profile=profile,
        )
        assert result.timed_out
        assert not result.success
        assert result.duration_seconds < 3.0  # killed within ~1s + overhead

    def test_fast_execution_completes(self, executor: SandboxExecutor):
        profile = SandboxProfile(
            profile_id="test-fast",
            max_duration_seconds=10.0,
        )
        result = executor.run_python(
            code="print('done')",
            profile=profile,
        )
        assert result.success
        assert not result.timed_out


# ─── Resource limits ──────────────────────────────────────


class TestSandboxResourceLimits:
    def test_memory_limit(self, executor: SandboxExecutor):
        profile = SandboxProfile(
            profile_id="test-mem",
            max_memory_mb=32,
            max_duration_seconds=5.0,
        )
        result = executor.run_python(
            code="x = bytearray(256 * 1024 * 1024)",  # 256MB
            profile=profile,
        )
        assert not result.success
        # Memory limit hit — either MemoryError or signal
        assert "MemoryError" in result.stderr or result.resource_exceeded

    def test_file_size_limit(self, executor: SandboxExecutor):
        profile = SandboxProfile(
            profile_id="test-fsize",
            max_duration_seconds=5.0,
        )
        result = executor.run_python(
            code="open('big.txt', 'w').write('x' * (100 * 1024 * 1024))",
            profile=profile,
        )
        assert not result.success  # Should fail on file size limit


# ─── Filesystem isolation ─────────────────────────────────


class TestSandboxFilesystem:
    def test_writes_within_sandbox(self, executor: SandboxExecutor):
        result = executor.run_python(
            code="open('output.txt', 'w').write('ok'); print(open('output.txt').read())",
        )
        assert result.success
        assert "ok" in result.stdout

    def test_pathTraversal_blocked(self, executor: SandboxExecutor):
        with pytest.raises(SandboxViolation):
            executor.run_python(
                code="print('hi')",
                extra_files={"../../etc/passwd": "malicious"},
            )

    def test_cwd_is_sandbox(self, executor: SandboxExecutor):
        result = executor.run_python(
            code="import os; print(os.getcwd())",
        )
        assert result.success
        assert "eon_sb" in result.stdout or "/tmp" in result.stdout.lower()


# ─── Env isolation ───────────────────────────────────────


class TestSandboxEnv:
    def test_secret_not_inherited(self, executor: SandboxExecutor):
        os.environ["EON_TEST_SECRET"] = "super-secret-value"
        try:
            result = executor.run_python(
                code="import os; print(os.environ.get('EON_TEST_SECRET', 'NOT_FOUND'))",
            )
            assert result.success
            assert "super-secret-value" not in result.stdout
            assert "NOT_FOUND" in result.stdout
        finally:
            del os.environ["EON_TEST_SECRET"]

    def test_env_vars_allowed(self, executor: SandboxExecutor):
        os.environ["EON_ALLOWED_VAR"] = "allowed-value"
        try:
            profile = SandboxProfile(
                profile_id="test-env",
                env_vars_allowed=["EON_ALLOWED_VAR"],
                max_duration_seconds=5.0,
            )
            result = executor.run_python(
                code="import os; print(os.environ.get('EON_ALLOWED_VAR', 'NOT_FOUND'))",
                profile=profile,
            )
            assert result.success
            assert "allowed-value" in result.stdout
        finally:
            del os.environ["EON_ALLOWED_VAR"]


# ─── Network blocking ─────────────────────────────────────


class TestSandboxNetwork:
    def test_network_blocked_for_python(self, executor: SandboxExecutor):
        profile = SandboxProfile(
            profile_id="no-network",
            network_allowed=False,
            max_duration_seconds=5.0,
        )
        result = executor.run_python(
            code="""
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("8.8.8.8", 53))
    print("NETWORK_ACCESSIBLE")
except PermissionError as e:
    print("NETWORK_BLOCKED")
except Exception as e:
    print(f"OTHER_ERROR: {e}")
""",
            profile=profile,
        )
        assert result.success
        # El socket blocker debe bloquear la conexión
        assert "NETWORK_BLOCKED" in result.stdout or "OTHER_ERROR" in result.stdout
        assert "NETWORK_ACCESSIBLE" not in result.stdout

    def test_network_isolation_flag(self, executor: SandboxExecutor):
        profile = SandboxProfile(
            profile_id="no-net",
            network_allowed=False,
        )
        result = executor.run_python(
            code="print('ok')",
            profile=profile,
        )
        assert result.network_isolation_enforced


# ─── Run command ──────────────────────────────────────────


class TestSandboxCommand:
    def test_run_echo(self, executor: SandboxExecutor):
        result = executor.run_command(["echo", "hello"])
        assert result.success
        assert "hello" in result.stdout

    def test_run_command_timeout(self, executor: SandboxExecutor):
        profile = SandboxProfile(
            profile_id="cmd-timeout",
            max_duration_seconds=1.0,
        )
        result = executor.run_command(["sleep", "10"], profile=profile)
        assert result.timed_out
        assert result.duration_seconds < 3.0
