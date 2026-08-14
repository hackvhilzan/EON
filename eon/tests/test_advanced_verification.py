"""
Tests de Verificación Avanzada (Fase 8).

Criterios de aceptación:
- CompositeVerifier combina múltiples capas
- StructuralVerifier: tasks completadas, artefactos
- CriteriaVerifier: criterios medibles y no medibles
- LLMJudgeVerifier: con judge mockeable
- TestBasedVerifier: genera y ejecuta tests
- ExternalVerifier: comandos externos
- ConfidenceCalibrator: pesos configurables
- EvidenceGraph: estructura de grafo
- Una capa FAILED crítica falla toda la verificación
- Capas SKIPPED no afectan el resultado
"""

from __future__ import annotations

import pytest

from eon.verification import (
    CompositeVerifier,
    ConfidenceCalibrator,
    CriteriaVerifier,
    EvidenceGraph,
    EvidenceNode,
    ExternalVerifier,
    LayerResult,
    LLMJudgeVerifier,
    StructuralVerifier,
    TestBasedVerifier,
    VerificationStatus,
)

# ─── StructuralVerifier Tests ─────────────────────────────


class TestStructuralVerifier:
    def test_all_tasks_completed(self):
        v = StructuralVerifier()
        result = v.verificar(
            objective=None,
            evidence={"tasks_totales": 5, "tasks_completadas": 5, "tasks_fallidas": 0},
        )
        assert result.status == VerificationStatus.PASSED
        assert result.confidence == 1.0

    def test_partial_completion(self):
        v = StructuralVerifier()
        result = v.verificar(
            objective=None,
            evidence={"tasks_totales": 5, "tasks_completadas": 3, "tasks_fallidas": 2},
        )
        assert result.status == VerificationStatus.FAILED
        assert "3/5" in result.motivo

    def test_no_tasks(self):
        v = StructuralVerifier()
        result = v.verificar(
            objective=None,
            evidence={"tasks_totales": 0, "tasks_completadas": 0},
        )
        assert result.status == VerificationStatus.SKIPPED

    def test_missing_artifacts(self):
        v = StructuralVerifier()
        result = v.verificar(
            objective=None,
            evidence={"tasks_totales": 2, "tasks_completadas": 2},
            artifacts={"output.txt": "hello"},
            expected_artifacts=["output.txt", "missing.txt"],
        )
        assert result.status == VerificationStatus.FAILED
        assert "missing.txt" in result.motivo

    def test_artifacts_present(self):
        v = StructuralVerifier()
        result = v.verificar(
            objective=None,
            evidence={"tasks_totales": 2, "tasks_completadas": 2},
            artifacts={"output.txt": "hello", "data.json": "{}"},
            expected_artifacts=["output.txt"],
        )
        assert result.status == VerificationStatus.PASSED


# ─── CriteriaVerifier Tests ─────────────────────────────────


class TestCriteriaVerifier:
    def test_no_criterio(self):
        v = CriteriaVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": ""},
            evidence={},
        )
        assert result.status == VerificationStatus.SKIPPED

    def test_contains_text(self):
        v = CriteriaVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "contiene 'hola mundo'"},
            evidence={},
            artifacts={"output.txt": "Esto es hola mundo en un archivo"},
        )
        assert result.status == VerificationStatus.PASSED
        assert result.confidence > 0.5

    def test_contains_text_not_found(self):
        v = CriteriaVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "contiene 'xyz123'"},
            evidence={},
            artifacts={"output.txt": "contenido normal"},
        )
        assert result.status == VerificationStatus.FAILED

    def test_size_requirement(self):
        v = CriteriaVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "archivo mayor a 100 bytes"},
            evidence={},
            artifacts={"output.txt": "x" * 200},
        )
        assert result.status == VerificationStatus.PASSED

    def test_non_measurable_criterio(self):
        v = CriteriaVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "resume el artículo de forma concisa"},
            evidence={},
            artifacts={},
        )
        assert result.status == VerificationStatus.SKIPPED

    def test_structured_criteria(self):
        v = CriteriaVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "archivo existe"},
            evidence={},
            artifacts={"output.pdf": "PDF content"},
            criterios_estructurados=[
                {"tipo": "artefacto_existe", "path": "output.pdf"},
            ],
        )
        assert result.status == VerificationStatus.PASSED


# ─── LLMJudgeVerifier Tests ────────────────────────────────


class TestLLMJudgeVerifier:
    def test_no_judge_configured(self):
        v = LLMJudgeVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "test"},
            evidence={},
        )
        assert result.status == VerificationStatus.SKIPPED

    def test_judge_approves(self):
        class MockJudge:
            def juzgar(self, criterio, resultado):
                return True, 0.92, "El resultado cumple el criterio"

        v = LLMJudgeVerifier(judge=MockJudge())
        result = v.verificar(
            objective={"criterio_de_exito": "genera un PDF"},
            evidence={"tasks_completadas": 3},
            artifacts={"output.pdf": "PDF content"},
        )
        assert result.status == VerificationStatus.PASSED
        assert result.confidence == 0.92
        assert "cumple" in result.motivo

    def test_judge_rejects(self):
        class MockJudge:
            def juzgar(self, criterio, resultado):
                return False, 0.3, "El PDF no tiene suficientes páginas"

        v = LLMJudgeVerifier(judge=MockJudge())
        result = v.verificar(
            objective={"criterio_de_exito": "PDF con >1 página"},
            evidence={},
            artifacts={"output.pdf": "PDF content"},
        )
        assert result.status == VerificationStatus.FAILED
        assert result.confidence == 0.3

    def test_judge_error(self):
        class BadJudge:
            def juzgar(self, criterio, resultado):
                raise RuntimeError("API error")

        v = LLMJudgeVerifier(judge=BadJudge())
        result = v.verificar(
            objective={"criterio_de_exito": "test"},
            evidence={},
        )
        assert result.status == VerificationStatus.ERROR


# ─── TestBasedVerifier Tests ───────────────────────────────


class TestBasedVerifierTests:
    def test_no_generator(self):
        v = TestBasedVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "test"},
            evidence={},
            artifacts={"code.py": "def foo(): pass"},
        )
        assert result.status == VerificationStatus.SKIPPED

    def test_no_artifacts(self):
        v = TestBasedVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "test"},
            evidence={},
        )
        assert result.status == VerificationStatus.SKIPPED

    def test_tests_pass(self):
        class MockGenerator:
            def generar_tests(self, criterio, content):
                return ["assert 'def' in _artifact"]

        v = TestBasedVerifier(generator=MockGenerator())
        result = v.verificar(
            objective={"criterio_de_exito": "contiene función"},
            evidence={},
            artifacts={"code.py": "def foo(): pass"},
        )
        assert result.status == VerificationStatus.PASSED
        assert result.confidence == 1.0

    def test_tests_fail(self):
        class MockGenerator:
            def generar_tests(self, criterio, content):
                return ["assert 'xyz123' in _artifact"]

        v = TestBasedVerifier(generator=MockGenerator())
        result = v.verificar(
            objective={"criterio_de_exito": "contiene xyz123"},
            evidence={},
            artifacts={"code.py": "def foo(): pass"},
        )
        assert result.status == VerificationStatus.FAILED


# ─── ExternalVerifier Tests ────────────────────────────────


class TestExternalVerifier:
    def test_no_commands_registered(self):
        v = ExternalVerifier()
        result = v.verificar(
            objective=None,
            evidence={},
            artifacts={"code.py": "x = 1"},
        )
        assert result.status == VerificationStatus.SKIPPED

    def test_capability_not_registered(self):
        v = ExternalVerifier()
        v.register_command("tool.python", ["python", "-c", "pass"])
        result = v.verificar(
            objective=None,
            evidence={},
            artifacts={"code.py": "x = 1"},
            capability_id="tool.filesystem",
        )
        assert result.status == VerificationStatus.SKIPPED

    def test_command_passes(self):
        v = ExternalVerifier()
        v.register_command("tool.python", ["python", "-c", "import sys; sys.exit(0)"])
        result = v.verificar(
            objective=None,
            evidence={},
            artifacts={"code.py": "x = 1"},
            capability_id="tool.python",
        )
        assert result.status == VerificationStatus.PASSED

    def test_command_fails(self):
        v = ExternalVerifier()
        v.register_command("tool.python", ["python", "-c", "import sys; sys.exit(1)"])
        result = v.verificar(
            objective=None,
            evidence={},
            artifacts={"code.py": "x = 1"},
            capability_id="tool.python",
        )
        assert result.status == VerificationStatus.FAILED


# ─── ConfidenceCalibrator Tests ────────────────────────────


class TestConfidenceCalibrator:
    def test_default_weights(self):
        c = ConfidenceCalibrator()
        weights = c.weights
        assert "structural" in weights
        assert "llm_judge" in weights
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_all_passed(self):
        c = ConfidenceCalibrator()
        layers = [
            LayerResult("structural", VerificationStatus.PASSED, confidence=1.0),
            LayerResult("criteria", VerificationStatus.PASSED, confidence=0.9),
            LayerResult("llm_judge", VerificationStatus.PASSED, confidence=0.95),
        ]
        confianza = c.calibrate(layers)
        assert confianza > 0.9

    def test_one_failed(self):
        c = ConfidenceCalibrator()
        layers = [
            LayerResult("structural", VerificationStatus.PASSED, confidence=1.0),
            LayerResult("criteria", VerificationStatus.FAILED, confidence=0.2),
            LayerResult("llm_judge", VerificationStatus.PASSED, confidence=0.9),
        ]
        confianza = c.calibrate(layers)
        assert confianza < 0.8  # penalizado por el fallo

    def test_all_skipped(self):
        c = ConfidenceCalibrator()
        layers = [
            LayerResult("structural", VerificationStatus.SKIPPED),
            LayerResult("criteria", VerificationStatus.SKIPPED),
        ]
        confianza = c.calibrate(layers)
        assert confianza == 0.0

    def test_custom_weights(self):
        c = ConfidenceCalibrator(weights={"structural": 1.0, "criteria": 1.0})
        assert c.weights["structural"] == pytest.approx(0.5)
        assert c.weights["criteria"] == pytest.approx(0.5)

    def test_update_weights(self):
        c = ConfidenceCalibrator()
        c.update_weights({"structural": 0.5, "llm_judge": 0.5})
        assert c.weights["structural"] == pytest.approx(0.5)
        assert "criteria" not in c.weights or c.weights.get("criteria", 0) == 0.0


# ─── CompositeVerifier Tests ───────────────────────────────


class TestCompositeVerifier:
    def test_all_layers_pass(self):
        class MockJudge:
            def juzgar(self, c, r):
                return True, 0.9, "OK"

        v = CompositeVerifier()
        v.add_layer(StructuralVerifier())
        v.add_layer(CriteriaVerifier())
        v.add_layer(LLMJudgeVerifier(judge=MockJudge()))

        result = v.verificar(
            objective={"criterio_de_exito": "contiene 'hello'"},
            evidence={"tasks_totales": 3, "tasks_completadas": 3, "tasks_fallidas": 0},
            artifacts={"output.txt": "hello world"},
        )

        assert result.cumple is True
        assert result.confianza > 0.5
        assert len(result.layers) == 3
        assert all(layer.status == VerificationStatus.PASSED for layer in result.layers)

    def test_structural_failure_fails_all(self):
        v = CompositeVerifier()
        v.add_layer(StructuralVerifier())
        v.add_layer(CriteriaVerifier())

        result = v.verificar(
            objective={"criterio_de_exito": "test"},
            evidence={"tasks_totales": 5, "tasks_completadas": 2, "tasks_fallidas": 3},
            artifacts={},
        )

        assert result.cumple is False

    def test_fail_fast(self):
        v = CompositeVerifier(fail_fast=True)
        v.add_layer(StructuralVerifier())
        v.add_layer(CriteriaVerifier())

        result = v.verificar(
            objective={"criterio_de_exito": "test"},
            evidence={"tasks_totales": 5, "tasks_completadas": 2, "tasks_fallidas": 3},
        )

        # fail_fast: solo se ejecutó structural (que falló)
        assert len(result.layers) == 1

    def test_no_layers(self):
        v = CompositeVerifier()
        result = v.verificar(
            objective={"criterio_de_exito": "test"},
            evidence={},
        )
        assert result.cumple is False
        assert result.confianza == 0.0

    def test_evidence_graph_built(self):
        v = CompositeVerifier()
        v.add_layer(StructuralVerifier())

        result = v.verificar(
            objective=None,
            evidence={"tasks_totales": 2, "tasks_completadas": 2},
            artifacts={"output.txt": "hello"},
        )

        assert result.evidence_graph is not None
        assert "nodes" in result.evidence_graph
        assert "evidence" in result.evidence_graph["nodes"]
        assert "artifact:output.txt" in result.evidence_graph["nodes"]
        assert "layer:structural" in result.evidence_graph["nodes"]

    def test_error_in_layer(self):
        class BadVerifier:
            name = "bad"

            def verificar(self, **kwargs):
                raise RuntimeError("boom")

        v = CompositeVerifier()
        v.add_layer(StructuralVerifier())
        v.add_layer(BadVerifier())

        result = v.verificar(
            objective=None,
            evidence={"tasks_totales": 1, "tasks_completadas": 1},
        )

        assert any(layer.status == VerificationStatus.ERROR for layer in result.layers)

    def test_skipped_layers_dont_fail(self):
        v = CompositeVerifier()
        v.add_layer(StructuralVerifier())  # pasará
        v.add_layer(CriteriaVerifier())  # skipped (criterio no medible)
        v.add_layer(LLMJudgeVerifier())  # skipped (no judge)

        result = v.verificar(
            objective={"criterio_de_exito": "algo subjetivo"},
            evidence={"tasks_totales": 2, "tasks_completadas": 2},
            artifacts={},
        )

        # Structural passed, others skipped → cumple
        assert result.cumple is True


# ─── EvidenceGraph Tests ──────────────────────────────────


class TestEvidenceGraph:
    def test_add_nodes_and_edges(self):
        g = EvidenceGraph()
        n1 = EvidenceNode("root", "evidence", value={"ok": True})
        n2 = EvidenceNode("child1", "artifact", value="data")
        g.add_node(n1)
        g.add_node(n2)
        g.add_edge("root", "child1")

        assert "root" in g.nodes
        assert "child1" in g.nodes
        assert "child1" in g.nodes["root"].children

    def test_to_dict(self):
        g = EvidenceGraph()
        g.add_node(EvidenceNode("n1", "task", value="test"))
        d = g.to_dict()
        assert "nodes" in d
        assert "n1" in d["nodes"]
        assert d["nodes"]["n1"]["node_type"] == "task"
