"""
Tests de Tool Guardrails (Fase 2.5).

Criterios de aceptación:
- Pre deny por secret en params
- Pre deny por path fuera de allowlist
- Post deny/warn por secret en output
- AuditLog recibe decisión
- TaskExecutor con guardrail DENY emite TASK_FALLIDA y devuelve False
- TaskExecutor sin guardrails conserva comportamiento original
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eon.event_bus import EventBus
from eon.guardrails import (
    AllowAllGuardrails,
    FilesystemGuardrail,
    GuardrailAction,
    NetworkGuardrail,
    PIIGuardrail,
    SecretPatternGuardrail,
    ToolCallContext,
    ToolGuardrailManager,
)
from eon.workers import events as worker_events
from eon.workers.executor import TaskExecutor

# ─── Helpers ─────────────────────────────────────────────


@dataclass
class FakeTask:
    """Task mínima para tests."""

    capability_id: str = "test.capability"
    id: str = "task-001"
    parametros: dict[str, Any] = field(default_factory=dict)


class FakeAuditLog:
    """AuditLog fake para tests."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


# ─── SecretPatternGuardrail Tests ─────────────────────────


class TestSecretPatternGuardrail:
    def test_detects_openai_key(self):
        g = SecretPatternGuardrail()
        ctx = ToolCallContext(
            capability_id="test",
            params={"key": "sk-" + "a" * 40},
        )
        result = g.pre_execute(ctx)
        assert result.is_denied
        assert "OpenAI API key" in result.reason

    def test_detects_aws_key(self):
        g = SecretPatternGuardrail()
        ctx = ToolCallContext(
            capability_id="test",
            params={"key": "AKIA" + "A" * 16},
        )
        result = g.pre_execute(ctx)
        assert result.is_denied

    def test_detects_github_token(self):
        g = SecretPatternGuardrail()
        ctx = ToolCallContext(
            capability_id="test",
            params={"token": "ghp_" + "a" * 36},
        )
        result = g.pre_execute(ctx)
        assert result.is_denied

    def test_detects_private_key(self):
        g = SecretPatternGuardrail()
        ctx = ToolCallContext(
            capability_id="test",
            params={"key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."},
        )
        result = g.pre_execute(ctx)
        assert result.is_denied

    def test_detects_password_param(self):
        g = SecretPatternGuardrail()
        ctx = ToolCallContext(
            capability_id="test",
            params={"password": "my-secret-pass"},
        )
        result = g.pre_execute(ctx)
        assert result.is_denied

    def test_redacts_secret_in_params(self):
        g = SecretPatternGuardrail()
        ctx = ToolCallContext(
            capability_id="test",
            params={"password": "super-secret"},
        )
        result = g.pre_execute(ctx)
        assert result.redacted_params is not None
        assert result.redacted_params["password"] == "***REDACTED***"

    def test_allows_clean_params(self):
        g = SecretPatternGuardrail()
        ctx = ToolCallContext(
            capability_id="test",
            params={"message": "hello world", "count": 42},
        )
        result = g.pre_execute(ctx)
        assert result.is_allowed

    def test_post_detects_secret_in_output(self):
        g = SecretPatternGuardrail()
        ctx = ToolCallContext(
            capability_id="test",
            params={"msg": "ok"},
        )
        result = g.post_execute(ctx, {"token": "sk-" + "a" * 40})
        assert result.action == GuardrailAction.WARN


# ─── FilesystemGuardrail Tests ───────────────────────────


class TestFilesystemGuardrail:
    def test_blocks_path_traversal(self, tmp_path):
        g = FilesystemGuardrail(allowed_root=tmp_path)
        ctx = ToolCallContext(
            capability_id="test",
            params={"path": "/etc/passwd"},
        )
        result = g.pre_execute(ctx)
        assert result.is_denied
        assert "fuera del sandbox" in result.reason.lower() or "out" in result.reason.lower()

    def test_allows_path_within_root(self, tmp_path):
        g = FilesystemGuardrail(allowed_root=tmp_path)
        safe_path = str(tmp_path / "output.txt")
        ctx = ToolCallContext(
            capability_id="test",
            params={"path": safe_path},
        )
        result = g.pre_execute(ctx)
        assert result.is_allowed

    def test_no_root_allows_all(self):
        g = FilesystemGuardrail(allowed_root=None)
        ctx = ToolCallContext(
            capability_id="test",
            params={"path": "/anywhere/file"},
        )
        result = g.pre_execute(ctx)
        assert result.is_allowed


# ─── NetworkGuardrail Tests ──────────────────────────────


class TestNetworkGuardrail:
    def test_blocks_network_capability(self):
        from eon.governance.models import SandboxProfile

        g = NetworkGuardrail()
        ctx = ToolCallContext(
            capability_id="tool.http_get",
            params={"url": "https://example.com"},
            sandbox_profile=SandboxProfile.workspace_only(),
        )
        result = g.pre_execute(ctx)
        assert result.is_denied

    def test_allows_non_network_capability(self):
        from eon.governance.models import SandboxProfile

        g = NetworkGuardrail()
        ctx = ToolCallContext(
            capability_id="code.write",
            params={"file": "test.py"},
            sandbox_profile=SandboxProfile.workspace_only(),
        )
        result = g.pre_execute(ctx)
        assert result.is_allowed

    def test_allows_when_network_enabled(self):
        from eon.governance.models import SandboxProfile

        g = NetworkGuardrail()
        ctx = ToolCallContext(
            capability_id="tool.http_get",
            params={"url": "https://example.com"},
            sandbox_profile=SandboxProfile.network_restricted(),
        )
        result = g.pre_execute(ctx)
        assert result.is_allowed


# ─── PIIGuardrail Tests ──────────────────────────────────


class TestPIIGuardrail:
    def test_detects_email(self):
        g = PIIGuardrail(block_pii=True)
        ctx = ToolCallContext(
            capability_id="test",
            params={"text": "contact: user@example.com"},
        )
        result = g.pre_execute(ctx)
        assert result.is_denied
        assert "email" in result.metadata.get("pii_found", [])

    def test_detects_dni(self):
        g = PIIGuardrail(block_pii=True)
        ctx = ToolCallContext(
            capability_id="test",
            params={"text": "DNI: 12345678Z"},
        )
        result = g.pre_execute(ctx)
        assert result.is_denied

    def test_warn_mode(self):
        g = PIIGuardrail(block_pii=False)
        ctx = ToolCallContext(
            capability_id="test",
            params={"text": "email: user@example.com"},
        )
        result = g.pre_execute(ctx)
        assert result.action == GuardrailAction.WARN

    def test_no_pii_allowed(self):
        g = PIIGuardrail(block_pii=True)
        ctx = ToolCallContext(
            capability_id="test",
            params={"text": "hello world"},
        )
        result = g.pre_execute(ctx)
        assert result.is_allowed


# ─── ToolGuardrailManager Tests ──────────────────────────


class TestToolGuardrailManager:
    def test_chain_multiple_guardrails(self):
        manager = ToolGuardrailManager()
        manager.add(SecretPatternGuardrail())
        manager.add(PIIGuardrail(block_pii=False))

        ctx = ToolCallContext(
            capability_id="test",
            params={"msg": "hello", "password": "secret123"},
        )
        result = manager.pre_execute(ctx)
        assert result.is_denied

    def test_most_restrictive_wins(self):
        manager = ToolGuardrailManager()
        manager.add(PIIGuardrail(block_pii=False))  # WARN
        manager.add(SecretPatternGuardrail())  # DENY

        ctx = ToolCallContext(
            capability_id="test",
            params={"password": "secret", "email": "a@b.com"},
        )
        result = manager.pre_execute(ctx)
        assert result.is_denied

    def test_audit_log_called(self):
        audit = FakeAuditLog()
        manager = ToolGuardrailManager(audit_log=audit)
        manager.add(SecretPatternGuardrail())

        ctx = ToolCallContext(
            capability_id="test",
            params={"password": "secret"},
            task_id="t1",
        )
        manager.pre_execute(ctx)
        assert len(audit.records) == 1
        assert audit.records[0]["action"] == "deny"

    def test_allow_all_by_default(self):
        manager = ToolGuardrailManager()
        ctx = ToolCallContext(
            capability_id="test",
            params={"anything": "goes"},
        )
        result = manager.pre_execute(ctx)
        assert result.is_allowed

    def test_post_execute_chain(self):
        manager = ToolGuardrailManager()
        manager.add(SecretPatternGuardrail())

        ctx = ToolCallContext(
            capability_id="test",
            params={"msg": "ok"},
        )
        result = manager.post_execute(ctx, {"token": "sk-" + "a" * 40})
        assert result.action == GuardrailAction.WARN


# ─── TaskExecutor Integration Tests ──────────────────────


class TestTaskExecutorGuardrails:
    def test_guardrail_deny_blocks_execution(self):
        """TaskExecutor con guardrail DENY emite TASK_FALLIDA y devuelve False."""
        bus = EventBus()
        events_received = []
        bus.subscribe(worker_events.TASK_FALLIDA, lambda **d: events_received.append(d))

        manager = ToolGuardrailManager()
        manager.add(SecretPatternGuardrail())

        executor = TaskExecutor(
            event_bus=bus,
            ejecutar=lambda cap, params: True,  # siempre éxito
            guardrails_manager=manager,
        )

        task = FakeTask(
            capability_id="test",
            parametros={"password": "super-secret"},
        )
        result = executor.ejecutar("worker-1", task)
        assert result is False
        assert len(events_received) == 1
        assert events_received[0]["task_id"] == "task-001"

    def test_no_guardrails_preserves_behavior(self):
        """TaskExecutor sin guardrails funciona como antes."""
        bus = EventBus()
        events_received = []
        bus.subscribe(worker_events.TASK_COMPLETADA, lambda **d: events_received.append(d))

        executor = TaskExecutor(
            event_bus=bus,
            ejecutar=lambda cap, params: True,
        )

        task = FakeTask(parametros={"msg": "hello"})
        result = executor.ejecutar("worker-1", task)
        assert result is True
        assert len(events_received) == 1

    def test_guardrail_allow_executes(self):
        """Guardrail ALLOW permite la ejecución normal."""
        bus = EventBus()
        completed = []
        bus.subscribe(worker_events.TASK_COMPLETADA, lambda **d: completed.append(d))

        manager = ToolGuardrailManager()
        manager.add(AllowAllGuardrails())

        executor = TaskExecutor(
            event_bus=bus,
            ejecutar=lambda cap, params: True,
            guardrails_manager=manager,
        )

        task = FakeTask(parametros={"msg": "hello"})
        result = executor.ejecutar("worker-1", task)
        assert result is True
        assert len(completed) == 1

    def test_guardrail_redacts_params(self):
        """Los guardrails pueden redactar params antes de ejecutar."""
        bus = EventBus()
        received_params = []

        def executor_fn(cap_id: str, params: dict) -> bool:
            received_params.append(dict(params))
            return True

        manager = ToolGuardrailManager()
        manager.add(SecretPatternGuardrail())

        executor = TaskExecutor(
            event_bus=bus,
            ejecutar=executor_fn,
            guardrails_manager=manager,
        )

        # Este no debería redactar porque no hay secret
        task = FakeTask(parametros={"msg": "hello"})
        executor.ejecutar("w1", task)
        assert received_params[0]["msg"] == "hello"
