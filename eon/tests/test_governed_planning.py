"""Tests de Fase 4 — Planificación inteligente gobernada.

Cubre el ``GovernedTaskGenerator`` que encadena:
    LLMTaskGenerator → TaskSpecValidator → PolicyEngine (preflight)

Escenarios:
- LLM devuelve TaskSpecs válidas → ejecución completa
- LLM devuelve JSON inválido → rechazo antes del Planner
- LLM propone capability desconocida → rechazo por TaskSpecValidator
- LLM propone capability denegada → rechazo por PolicyEngine preflight
- LLM propone dependencias inválidas → rechazo por TaskSpecValidator
- Sin PolicyEngine, se comporta como ValidatingTaskGenerator
- Sin GovernedTaskGenerator, el runtime funciona igual que antes
- Integración con KernelRuntime: planificación gobernada de punta a punta
"""

from __future__ import annotations

import json
import tempfile

import pytest

from eon.governance import (
    CapabilityPolicy,
    PolicyDecision,
    PolicyEngine,
    SandboxProfile,
)
from eon.llm.base import LLM
from eon.planner.task import Task
from eon.runtime import KernelRuntime
from eon.task_generation import (
    GovernedTaskGenerator,
    GovernedTaskRejectionError,
    LLMTaskGenerator,
    TaskSpecValidator,
)
from eon.task_generation.governed import GovernedTaskRejectionError

# ─── Fake LLM para tests ─────────────────────────────────────────


class FakeLLM(LLM):
    """LLM que devuelve un JSON preconfigurado."""

    name = "fake"

    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, prompt: str) -> str:
        return self._response


class FailingLLM(LLM):
    """LLM que simula un fallo de red."""

    name = "failing"

    def generate(self, prompt: str) -> str:
        raise ConnectionError("LLM no disponible")


# ─── Objetivo de prueba ──────────────────────────────────────────


class FakeObjective:
    """Objetivo mínimo para tests."""

    def __init__(
        self,
        descripcion: str = "Procesar archivos del directorio",
        criterio_de_exito: str = "El resultado contiene al menos un archivo",
        id: str = "obj-test-1",
    ) -> None:
        self.descripcion = descripcion
        self.criterio_de_exito = criterio_de_exito
        self.id = id
        self.estado = "pendiente"


# ─── GovernedTaskGenerator unit tests ─────────────────────────────


class TestGovernedTaskGenerator:
    """Tests unitarios del GovernedTaskGenerator."""

    def test_llm_valido_genera_specs_aprobadas(self):
        """LLM devuelve TaskSpecs válidas → todas pasan validación y preflight."""
        llm = FakeLLM(
            json.dumps(
                [
                    {"capability_id": "default", "id": "t1"},
                ]
            )
        )
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-default",
                    capabilities={"default"},
                    decision=PolicyDecision.ALLOW,
                )
            ]
        )
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default"]),
            validator=TaskSpecValidator(capabilities_validas=["default"]),
            policy_engine=engine,
        )
        specs = gen.generar(FakeObjective())
        assert len(specs) == 1
        assert specs[0].capability_id == "default"

    def test_llm_json_invalido_rechaza_antes_del_planner(self):
        """LLM devuelve JSON inválido → ValueError antes del Planner."""
        llm = FakeLLM("esto no es json")
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm),
            validator=TaskSpecValidator(),
        )
        with pytest.raises(ValueError, match="no es JSON válido"):
            gen.generar(FakeObjective())

    def test_llm_propone_capability_desconocida_rechaza(self):
        """LLM propone capability no en allowlist → TaskSpecValidationError."""
        llm = FakeLLM(json.dumps([{"capability_id": "tool.terminal", "id": "t1"}]))
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default"]),
            validator=TaskSpecValidator(capabilities_validas=["default"]),
        )
        with pytest.raises(Exception, match="no está en el conjunto permitido"):
            gen.generar(FakeObjective())

    def test_llm_propone_capability_denegada_rechaza_en_preflight(self):
        """LLM propone capability denegada por PolicyEngine → GovernedTaskRejectionError."""
        llm = FakeLLM(json.dumps([{"capability_id": "tool.terminal", "id": "t1"}]))
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
        # El validator permite tool.terminal, pero el PolicyEngine la deniega
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default", "tool.terminal"]),
            validator=TaskSpecValidator(capabilities_validas=["default", "tool.terminal"]),
            policy_engine=engine,
        )
        with pytest.raises(GovernedTaskRejectionError) as exc_info:
            gen.generar(FakeObjective())
        # El rechazo incluye detalles
        assert len(exc_info.value.rejected) == 1
        assert exc_info.value.rejected[0]["capability_id"] == "tool.terminal"
        assert exc_info.value.rejected[0]["policy_id"] == "deny-terminal"

    def test_llm_propone_dependencias_invalidas_rechaza(self):
        """LLM propone dependencias a tasks inexistentes → TaskSpecValidationError."""
        llm = FakeLLM(
            json.dumps(
                [
                    {"capability_id": "default", "id": "t1", "depende_de": ["t-inexistente"]},
                ]
            )
        )
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default"]),
            validator=TaskSpecValidator(capabilities_validas=["default"]),
        )
        with pytest.raises(Exception, match="no existe en el lote"):
            gen.generar(FakeObjective())

    def test_sin_policy_engine_se_comporta_como_validating(self):
        """Sin PolicyEngine, se comporta como ValidatingTaskGenerator."""
        llm = FakeLLM(json.dumps([{"capability_id": "default", "id": "t1"}]))
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default"]),
            validator=TaskSpecValidator(capabilities_validas=["default"]),
            policy_engine=None,
        )
        specs = gen.generar(FakeObjective())
        assert len(specs) == 1

    def test_specs_mixtas_algunas_denegadas(self):
        """Si algunas specs pasan y otras no, se rechaza todo el lote."""
        llm = FakeLLM(
            json.dumps(
                [
                    {"capability_id": "default", "id": "t1"},
                    {"capability_id": "tool.terminal", "id": "t2"},
                ]
            )
        )
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="allow-default",
                    capabilities={"default"},
                    decision=PolicyDecision.ALLOW,
                ),
                CapabilityPolicy(
                    policy_id="deny-terminal",
                    capabilities={"tool.terminal"},
                    decision=PolicyDecision.DENY,
                    reason="terminal no permitido",
                ),
            ]
        )
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default", "tool.terminal"]),
            validator=TaskSpecValidator(capabilities_validas=["default", "tool.terminal"]),
            policy_engine=engine,
        )
        with pytest.raises(GovernedTaskRejectionError) as exc_info:
            gen.generar(FakeObjective())
        # Solo t2 fue rechazada
        assert len(exc_info.value.rejected) == 1
        assert exc_info.value.rejected[0]["task_id"] == "t2"

    def test_requires_approval_tambien_rechaza(self):
        """REQUIRES_APPROVAL en preflight se trata como rechazo."""
        llm = FakeLLM(json.dumps([{"capability_id": "tool.llm", "id": "t1"}]))
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="approve-llm",
                    capabilities={"tool.llm"},
                    decision=PolicyDecision.REQUIRES_APPROVAL,
                    reason="requiere aprobación humana",
                )
            ]
        )
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default", "tool.llm"]),
            validator=TaskSpecValidator(capabilities_validas=["default", "tool.llm"]),
            policy_engine=engine,
        )
        with pytest.raises(GovernedTaskRejectionError) as exc_info:
            gen.generar(FakeObjective())
        assert exc_info.value.rejected[0]["decision"] == "requires_approval"

    def test_multiples_specs_todas_permitidas(self):
        """Múltiples specs, todas permitidas → todas pasan."""
        llm = FakeLLM(
            json.dumps(
                [
                    {"capability_id": "default", "id": "t1"},
                    {"capability_id": "default", "id": "t2", "depende_de": ["t1"]},
                ]
            )
        )
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
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default"]),
            validator=TaskSpecValidator(capabilities_validas=["default"]),
            policy_engine=engine,
        )
        specs = gen.generar(FakeObjective())
        assert len(specs) == 2

    def test_rejection_error_tiene_detalles(self):
        """GovernedTaskRejectionError incluye task_id, capability, policy, decision, reason."""
        llm = FakeLLM(json.dumps([{"capability_id": "tool.python", "id": "t1"}]))
        engine = PolicyEngine(
            policies=[
                CapabilityPolicy(
                    policy_id="deny-python",
                    capabilities={"tool.python"},
                    decision=PolicyDecision.DENY,
                    reason="python no permitido",
                )
            ]
        )
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default", "tool.python"]),
            validator=TaskSpecValidator(capabilities_validas=["default", "tool.python"]),
            policy_engine=engine,
        )
        with pytest.raises(GovernedTaskRejectionError) as exc_info:
            gen.generar(FakeObjective())
        r = exc_info.value.rejected[0]
        assert r["task_id"] == "t1"
        assert r["capability_id"] == "tool.python"
        assert r["policy_id"] == "deny-python"
        assert r["decision"] == "deny"
        assert r["reason"] == "python no permitido"


# ─── KernelRuntime integration tests ─────────────────────────────


class TestGovernedPlanningIntegration:
    """Tests de integración del GovernedTaskGenerator con KernelRuntime."""

    def test_planificacion_gobernada_ejecucion_completa(self):
        """LLM propone tasks válidas, PolicyEngine las aprueba, ejecución completa."""
        llm = FakeLLM(json.dumps([{"capability_id": "default", "id": "t1"}]))
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
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default"]),
            validator=TaskSpecValidator(capabilities_validas=["default"]),
            policy_engine=engine,
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=lambda cap, params: True,
            task_generation=gen,
            worker_capabilities=["default"],
            policy_engine=engine,
        )
        result = runtime.run(
            descripcion="Procesar archivos",
            criterio_de_exito="El resultado es exitoso",
        )
        assert result.execution_id is not None
        assert result.package_state == "ready"

    def test_planificacion_gobernada_capability_denegada_falla(self):
        """LLM propone capability denegada → la ejecución falla antes del Plan."""
        llm = FakeLLM(json.dumps([{"capability_id": "tool.terminal", "id": "t1"}]))
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
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default", "tool.terminal"]),
            validator=TaskSpecValidator(capabilities_validas=["default", "tool.terminal"]),
            policy_engine=engine,
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=lambda cap, params: True,
            task_generation=gen,
            worker_capabilities=["default", "tool.terminal"],
            policy_engine=engine,
        )
        # La ejecución falla porque el preflight del GovernedTaskGenerator rechaza
        with pytest.raises(Exception):
            runtime.run(
                descripcion="Ejecutar comando",
                criterio_de_exito="Comando ejecutado",
            )

    def test_sin_governed_generator_todo_funciona(self):
        """Sin GovernedTaskGenerator, el runtime funciona igual que antes."""
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

    def test_doble_gate_preflight_y_runtime(self):
        """El preflight (GovernedTaskGenerator) y el gate de runtime
        (KernelRuntime._drain_dispatch_queue) ambos evalúan las tasks."""
        llm = FakeLLM(json.dumps([{"capability_id": "default", "id": "t1"}]))
        # El mismo PolicyEngine se usa en ambos gates
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
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(llm, capabilities_validas=["default"]),
            validator=TaskSpecValidator(capabilities_validas=["default"]),
            policy_engine=engine,
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=lambda cap, params: True,
            task_generation=gen,
            worker_capabilities=["default"],
            policy_engine=engine,
        )
        result = runtime.run(
            descripcion="Procesar archivos",
            criterio_de_exito="El resultado es exitoso",
        )
        # El AuditLog debe tener entradas del gate de runtime
        entries = runtime.audit_log.replay(result.execution_id)
        assert len(entries) >= 1
        assert any(e.decision == "allow" for e in entries)

    def test_llm_falla_graciosamente(self):
        """Si el LLM falla, la ejecución falla sin colgarse."""
        gen = GovernedTaskGenerator(
            inner=LLMTaskGenerator(FailingLLM(), capabilities_validas=["default"]),
            validator=TaskSpecValidator(capabilities_validas=["default"]),
        )
        runtime = KernelRuntime(
            root=tempfile.mkdtemp(),
            task_executor=lambda cap, params: True,
            task_generation=gen,
            worker_capabilities=["default"],
        )
        with pytest.raises(Exception):
            runtime.run(
                descripcion="Objetivo",
                criterio_de_exito="Resultado exitoso",
            )
