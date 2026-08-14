"""Tests del subsistema de Gobernanza (Fase 3).

Cubre:
- PolicyEngine: decidir(), default-deny, primera política que matcha gana.
- CapabilityPolicy: evaluar(), condición, capabilities no cubiertas.
- SandboxProfile: factory methods (workspace_only, read_only, network_restricted).
- AuditLog: registrar(), replay(), verificar_integridad(), cadena de hashes.
- Integración con KernelRuntime: capability permitida ejecuta, capability
  denegada falla antes de despachar, decisión queda auditada, eventos se emiten.
- Compatibilidad: sin policy_engine, todo sigue funcionando.
"""

from __future__ import annotations

import tempfile

import pytest

from eon.governance import (
    AuditEntry,
    AuditLog,
    CapabilityPolicy,
    ExecutionContext,
    PolicyDecision,
    PolicyEngine,
    SandboxProfile,
)
from eon.governance import events as governance_events
from eon.planner.task import Task
from eon.runtime import KernelRuntime

# ─── PolicyEngine unit tests ─────────────────────────────────────


class TestPolicyEngine:
    """Tests unitarios del PolicyEngine."""

    def test_deny_por_defecto_si_no_hay_politicas(self):
        """Sin políticas, el default-deny deniega cualquier capability."""
        engine = PolicyEngine()
        task = Task(capability_id="tool.filesystem")
        result = engine.decidir(task)
        assert result.decision is PolicyDecision.DENY
        assert result.policy_id == "default-deny"

    def test_capability_permitida_pasa(self):
        """Una capability en el allowlist se permite."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-fs",
                    capabilities={"tool.filesystem"},
                    decision=PolicyDecision.ALLOW,
                    sandbox_profile=SandboxProfile.workspace_only(),
                )
            ]
        )
        task = Task(capability_id="tool.filesystem")
        result = engine.decidir(task)
        assert result.decision is PolicyDecision.ALLOW
        assert result.policy_id == "allow-fs"
        assert result.sandbox_profile is not None

    def test_capability_denegada_no_pasa(self):
        """Una capability con política DENY no se permite."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="deny-terminal",
                    capabilities={"tool.terminal"},
                    decision=PolicyDecision.DENY,
                    reason="terminal no permitido",
                )
            ]
        )
        task = Task(capability_id="tool.terminal")
        result = engine.decidir(task)
        assert result.decision is PolicyDecision.DENY
        assert "terminal no permitido" in result.reason

    def test_capability_no_cubierta_cae_en_default_deny(self):
        """Una capability que ninguna política cubre se deniega por defecto."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-fs",
                    capabilities={"tool.filesystem"},
                    decision=PolicyDecision.ALLOW,
                )
            ]
        )
        task = Task(capability_id="tool.terminal")
        result = engine.decidir(task)
        assert result.decision is PolicyDecision.DENY
        assert result.policy_id == "default-deny"

    def test_primera_politica_que_matcha_gana(self):
        """Si dos políticas cubren la misma capability, la primera gana."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-fs",
                    capabilities={"tool.filesystem"},
                    decision=PolicyDecision.ALLOW,
                ),
                CapabilityPolicy(
                    policy_id="deny-fs",
                    capabilities={"tool.filesystem"},
                    decision=PolicyDecision.DENY,
                ),
            ]
        )
        task = Task(capability_id="tool.filesystem")
        result = engine.decidir(task)
        assert result.decision is PolicyDecision.ALLOW
        assert result.policy_id == "allow-fs"

    def test_condicion_no_cumplida_no_aplica(self):
        """Si la condición de la política no se cumple, se salta a la siguiente."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-fs-if-owner",
                    capabilities={"tool.filesystem"},
                    decision=PolicyDecision.ALLOW,
                    condition=lambda task, obj, ctx: ctx.usuario == "admin",
                ),
                CapabilityPolicy(
                    policy_id="deny-fs-default",
                    capabilities={"tool.filesystem"},
                    decision=PolicyDecision.DENY,
                    reason="usuario no es admin",
                ),
            ]
        )
        task = Task(capability_id="tool.filesystem")
        # Con usuario no-admin: la condición falla, pasa a la deny
        result = engine.decidir(task, context=ExecutionContext(usuario="guest"))
        assert result.decision is PolicyDecision.DENY
        assert result.policy_id == "deny-fs-default"

    def test_condicion_cumplida_permite(self):
        """Si la condición se cumple, la política aplica."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-fs-if-owner",
                    capabilities={"tool.filesystem"},
                    decision=PolicyDecision.ALLOW,
                    condition=lambda task, obj, ctx: ctx.usuario == "admin",
                ),
            ]
        )
        task = Task(capability_id="tool.filesystem")
        result = engine.decidir(task, context=ExecutionContext(usuario="admin"))
        assert result.decision is PolicyDecision.ALLOW
        assert result.policy_id == "allow-fs-if-owner"

    def test_agregar_politica(self):
        """Se pueden agregar políticas dinámicamente."""
        engine = PolicyEngine()
        task = Task(capability_id="tool.filesystem")
        # Sin políticas: deny
        assert engine.decidir(task).decision is PolicyDecision.DENY
        # Agregar política
        engine.agregar(
            CapabilityPolicy(
                policy_id="allow-fs",
                capabilities={"tool.filesystem"},
                decision=PolicyDecision.ALLOW,
            )
        )
        assert engine.decidir(task).decision is PolicyDecision.ALLOW

    def test_requires_approval(self):
        """REQUIRES_APPROVAL se respeta como decisión."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="approve-llm",
                    capabilities={"tool.llm"},
                    decision=PolicyDecision.REQUIRES_APPROVAL,
                    reason="LLM requiere aprobación humana",
                )
            ]
        )
        task = Task(capability_id="tool.llm")
        result = engine.decidir(task)
        assert result.decision is PolicyDecision.REQUIRES_APPROVAL


# ─── SandboxProfile tests ────────────────────────────────────────


class TestSandboxProfile:
    """Tests de los perfiles de sandbox predefinidos."""

    def test_workspace_only(self):
        profile = SandboxProfile.workspace_only()
        assert profile.profile_id == "workspace-only"
        assert profile.filesystem_writable is True
        assert profile.network_allowed is False
        assert profile.max_duration_seconds == 30.0

    def test_read_only(self):
        profile = SandboxProfile.read_only()
        assert profile.profile_id == "read-only"
        assert profile.filesystem_writable is False
        assert profile.network_allowed is False
        assert profile.max_duration_seconds == 15.0

    def test_network_restricted(self):
        allowlist = ["example.com", "api.example.com"]
        profile = SandboxProfile.network_restricted(allowlist=allowlist)
        assert profile.profile_id == "network-restricted"
        assert profile.network_allowed is True
        assert profile.network_allowlist == allowlist
        assert profile.max_duration_seconds == 60.0


# ─── AuditLog tests ──────────────────────────────────────────────


class TestAuditLog:
    """Tests del AuditLog append-only con cadena de hashes."""

    def test_registrar_crea_entrada(self):
        log = AuditLog()
        entry = log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_EVALUATED,
            decision="allow",
            policy_id="allow-fs",
            reason="capability permitida",
            task_id="task-1",
            capability_id="tool.filesystem",
        )
        assert len(log) == 1
        assert entry.execution_id == "exec-1"
        assert entry.decision == "allow"
        assert entry.entry_hash != ""
        assert entry.previous_hash == "genesis"

    def test_cadena_de_hashes(self):
        """Cada entrada incluye el hash de la anterior."""
        log = AuditLog()
        e1 = log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_EVALUATED,
            decision="allow",
            policy_id="p1",
            reason="ok",
        )
        e2 = log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_DENIED,
            decision="deny",
            policy_id="p2",
            reason="denied",
        )
        assert e1.previous_hash == "genesis"
        assert e2.previous_hash == e1.entry_hash
        assert e1.entry_hash != e2.entry_hash

    def test_replay_por_execution_id(self):
        """replay devuelve solo las entradas de esa ejecución, en orden."""
        log = AuditLog()
        log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_EVALUATED,
            decision="allow",
            policy_id="p1",
            reason="ok",
        )
        log.registrar(
            execution_id="exec-2",
            event_type=governance_events.POLICY_EVALUATED,
            decision="deny",
            policy_id="p2",
            reason="denied",
        )
        log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_DENIED,
            decision="deny",
            policy_id="p2",
            reason="denied",
        )
        entries = log.replay("exec-1")
        assert len(entries) == 2
        assert all(e.execution_id == "exec-1" for e in entries)
        assert entries[0].decision == "allow"
        assert entries[1].decision == "deny"

    def test_verificar_integridad_log_vacio(self):
        """Un log vacío tiene integridad válida."""
        log = AuditLog()
        assert log.verificar_integridad() is True

    def test_verificar_integridad_log_intacto(self):
        """Un log no modificado tiene integridad válida."""
        log = AuditLog()
        log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_EVALUATED,
            decision="allow",
            policy_id="p1",
            reason="ok",
        )
        log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_DENIED,
            decision="deny",
            policy_id="p2",
            reason="denied",
        )
        assert log.verificar_integridad() is True

    def test_verificar_integridad_detecta_modificacion(self):
        """Modificar una entrada pasada invalida la cadena."""
        log = AuditLog()
        log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_EVALUATED,
            decision="allow",
            policy_id="p1",
            reason="ok",
        )
        log.registrar(
            execution_id="exec-1",
            event_type=governance_events.POLICY_DENIED,
            decision="deny",
            policy_id="p2",
            reason="denied",
        )
        # Modificar la primera entrada
        original = log._entries[0]
        log._entries[0] = AuditEntry(
            timestamp=original.timestamp,
            execution_id=original.execution_id,
            event_type=original.event_type,
            task_id=original.task_id,
            capability_id=original.capability_id,
            decision="deny",  # cambiado de allow a deny
            policy_id=original.policy_id,
            reason=original.reason,
            sandbox_profile=original.sandbox_profile,
            previous_hash=original.previous_hash,
            entry_hash=original.entry_hash,
        )
        assert log.verificar_integridad() is False

    def test_persistencia_jsonl(self):
        """El log se persiste como JSONL y se puede recargar."""
        with tempfile.TemporaryDirectory() as tmp:
            log1 = AuditLog(root=tmp)
            log1.registrar(
                execution_id="exec-1",
                event_type=governance_events.POLICY_EVALUATED,
                decision="allow",
                policy_id="p1",
                reason="ok",
                task_id="task-1",
                capability_id="tool.filesystem",
            )
            assert len(log1) == 1

            # Recargar desde disco
            log2 = AuditLog(root=tmp)
            assert len(log2) == 1
            assert log2.entries[0].execution_id == "exec-1"
            assert log2.entries[0].decision == "allow"
            assert log2.verificar_integridad() is True


# ─── KernelRuntime integration tests ────────────────────────────


class TestGovernanceIntegration:
    """Tests de integración del PolicyEngine con KernelRuntime."""

    def test_sin_policy_engine_todo_funciona(self):
        """Sin policy_engine, el runtime se comporta exactamente igual que antes."""
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=lambda cap, params: True,
        )
        result = runtime.run(
            descripcion="Objetivo de prueba",
            criterio_de_exito="El resultado es exitoso",
        )
        assert result.execution_id is not None
        assert result.package_state == "ready"

    def test_capability_permitida_ejecuta_y_produce_package(self):
        """Una capability permitida por el PolicyEngine se ejecuta normalmente."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-default",
                    capabilities={"default"},
                    decision=PolicyDecision.ALLOW,
                    sandbox_profile=SandboxProfile.workspace_only(),
                )
            ]
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=lambda cap, params: True,
            policy_engine=engine,
        )
        result = runtime.run(
            descripcion="Objetivo de prueba",
            criterio_de_exito="El resultado es exitoso",
        )
        assert result.execution_id is not None
        assert result.package_state == "ready"

    def test_capability_denegada_no_ejecuta(self):
        """Una capability denegada por el PolicyEngine no se ejecuta."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="deny-default",
                    capabilities={"default"},
                    decision=PolicyDecision.DENY,
                    reason="capability denegada por política",
                )
            ]
        )
        call_count = 0

        def counting_executor(cap, params):
            nonlocal call_count
            call_count += 1
            return True

        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=counting_executor,
            policy_engine=engine,
        )
        # La ejecución falla porque la task es denegada
        with pytest.raises(Exception):
            runtime.run(
                descripcion="Objetivo de prueba",
                criterio_de_exito="El resultado es exitoso",
            )
        # El executor nunca fue llamado
        assert call_count == 0

    def test_decision_queda_auditada(self):
        """Toda decisión (allow o deny) se registra en el AuditLog."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-default",
                    capabilities={"default"},
                    decision=PolicyDecision.ALLOW,
                )
            ]
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=lambda cap, params: True,
            policy_engine=engine,
        )
        result = runtime.run(
            descripcion="Objetivo de prueba",
            criterio_de_exito="El resultado es exitoso",
        )
        # El AuditLog debe tener al menos una entrada policy_evaluated
        entries = runtime.audit_log.replay(result.execution_id)
        assert len(entries) >= 1
        assert any(e.event_type == governance_events.POLICY_EVALUATED for e in entries)
        assert any(e.decision == "allow" for e in entries)

    def test_denegacion_queda_auditada(self):
        """La denegación también se registra en el AuditLog."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="deny-default",
                    capabilities={"default"},
                    decision=PolicyDecision.DENY,
                    reason="denegada",
                )
            ]
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=lambda cap, params: True,
            policy_engine=engine,
        )
        try:
            runtime.run(
                descripcion="Objetivo de prueba",
                criterio_de_exito="El resultado es exitoso",
            )
        except Exception:
            pass  # Se espera que falle
        # El AuditLog debe tener entradas
        entries = runtime.audit_log.entries
        assert len(entries) >= 1
        assert any(e.decision == "deny" for e in entries)

    def test_evento_policy_evaluated_se_emite(self):
        """El evento POLICY_EVALUATED se emite en el EventBus."""
        emitted_events: list[str] = []
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-default",
                    capabilities={"default"},
                    decision=PolicyDecision.ALLOW,
                )
            ]
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=lambda cap, params: True,
            policy_engine=engine,
        )
        runtime._event_bus.subscribe(
            governance_events.POLICY_EVALUATED,
            lambda **kw: emitted_events.append(governance_events.POLICY_EVALUATED),
        )
        runtime.run(
            descripcion="Objetivo de prueba",
            criterio_de_exito="El resultado es exitoso",
        )
        assert governance_events.POLICY_EVALUATED in emitted_events

    def test_evento_policy_denied_se_emite(self):
        """El evento POLICY_DENIED se emite cuando se deniega una Task."""
        emitted: list[str] = []
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="deny-default",
                    capabilities={"default"},
                    decision=PolicyDecision.DENY,
                    reason="denegada",
                )
            ]
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=lambda cap, params: True,
            policy_engine=engine,
        )
        runtime._event_bus.subscribe(
            governance_events.POLICY_DENIED,
            lambda **kw: emitted.append(governance_events.POLICY_DENIED),
        )
        try:
            runtime.run(
                descripcion="Objetivo de prueba",
                criterio_de_exito="El resultado es exitoso",
            )
        except Exception:
            pass
        assert governance_events.POLICY_DENIED in emitted

    def test_replay_reconstruye_ejecucion(self):
        """AuditLog.replay(execution_id) devuelve las decisiones en orden."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-default",
                    capabilities={"default"},
                    decision=PolicyDecision.ALLOW,
                )
            ]
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=lambda cap, params: True,
            policy_engine=engine,
        )
        result = runtime.run(
            descripcion="Objetivo de prueba",
            criterio_de_exito="El resultado es exitoso",
        )
        entries = runtime.audit_log.replay(result.execution_id)
        assert len(entries) >= 1
        # Todas las entradas pertenecen a la misma ejecución
        assert all(e.execution_id == result.execution_id for e in entries)
        # La cadena de hashes es válida
        assert runtime.audit_log.verificar_integridad() is True

    def test_audit_log_cadena_de_hashes_verificable(self):
        """La cadena de hashes del AuditLog detecta manipulación."""
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-default",
                    capabilities={"default"},
                    decision=PolicyDecision.ALLOW,
                )
            ]
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            tasks=[Task(capability_id="default", id="t1")],
            task_executor=lambda cap, params: True,
            policy_engine=engine,
        )
        runtime.run(
            descripcion="Objetivo de prueba",
            criterio_de_exito="El resultado es exitoso",
        )
        # El log es íntegro
        assert runtime.audit_log.verificar_integridad() is True
        # Modificar una entrada
        if len(runtime.audit_log._entries) > 0:
            original = runtime.audit_log._entries[0]
            runtime.audit_log._entries[0] = AuditEntry(
                timestamp=original.timestamp,
                execution_id=original.execution_id,
                event_type=original.event_type,
                task_id=original.task_id,
                capability_id=original.capability_id,
                decision="deny",  # manipulado
                policy_id=original.policy_id,
                reason=original.reason,
                sandbox_profile=original.sandbox_profile,
                previous_hash=original.previous_hash,
                entry_hash=original.entry_hash,
            )
            assert runtime.audit_log.verificar_integridad() is False
