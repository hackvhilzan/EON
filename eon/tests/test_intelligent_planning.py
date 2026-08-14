"""
Tests de Planificación Inteligente (Fase 7).

Criterios de aceptación:
- PlanScorer: score plans, rank multiple plans
- PlanSimulator: predict success/cost/duration, detect risks
- ObjectiveDecomposer: decompose complex objectives, max_depth
- AutoReplanner: classify failures, select strategy, build modified tasks
"""

from __future__ import annotations

import pytest

from eon.planning import (
    AutoReplanner,
    ObjectiveDecomposer,
    PlanScore,
    PlanScorer,
    PlanSimulation,
    PlanSimulator,
    ReplanContext,
    SubObjective,
)

# ─── PlanScorer Tests ─────────────────────────────────────


class TestPlanScorer:
    def test_score_simple_plan(self):
        scorer = PlanScorer()
        plan = {
            "id": "plan-1",
            "tasks": [
                {"id": "t1", "description": "Buscar datos de ventas", "capability_id": "tool.web"},
                {"id": "t2", "description": "Generar PDF de ventas", "capability_id": "tool.pdf"},
            ],
        }
        score = scorer.score(plan, success_criteria="generar PDF de ventas")
        assert isinstance(score, PlanScore)
        assert score.plan_id == "plan-1"
        assert 0.0 <= score.total_score <= 1.0
        assert score.efficiency > 0
        assert score.coverage > 0

    def test_score_empty_plan(self):
        scorer = PlanScorer()
        score = scorer.score({"id": "empty", "tasks": []})
        assert score.total_score >= 0.0
        assert score.efficiency == 0.0

    def test_fewer_tasks_more_efficient(self):
        scorer = PlanScorer()
        plan_small = {
            "id": "small",
            "tasks": [{"id": "t1", "description": "do everything"}],
        }
        plan_large = {
            "id": "large",
            "tasks": [{"id": f"t{i}", "description": f"step {i}"} for i in range(10)],
        }
        score_small = scorer.score(plan_small)
        score_large = scorer.score(plan_large)
        assert score_small.efficiency > score_large.efficiency

    def test_rank_multiple_plans(self):
        scorer = PlanScorer()
        plans = [
            {"id": "p1", "tasks": [{"id": "t1", "description": "generar PDF ventas"}]},
            {"id": "p2", "tasks": [{"id": f"t{i}", "description": f"step {i}"} for i in range(10)]},
            {
                "id": "p3",
                "tasks": [
                    {"id": "t1", "description": "buscar ventas"},
                    {"id": "t2", "description": "generar PDF"},
                ],
            },
        ]
        ranked = scorer.rank(plans, success_criteria="generar PDF ventas")
        assert len(ranked) == 3
        assert ranked[0].total_score >= ranked[-1].total_score

    def test_coverage_matches_criteria(self):
        scorer = PlanScorer()
        plan = {
            "id": "plan-1",
            "tasks": [
                {"id": "t1", "description": "generar informe ventas trimestral", "capability_id": "tool.pdf"},
            ],
        }
        score = scorer.score(plan, success_criteria="generar informe ventas")
        assert score.coverage > 0.5

    def test_fallback_increases_robustness(self):
        scorer = PlanScorer()
        plan_no_fallback = {
            "id": "p1",
            "tasks": [{"id": "t1", "description": "task"}],
        }
        plan_with_fallback = {
            "id": "p2",
            "tasks": [{"id": "t1", "description": "task", "fallback": "tool.alt"}],
        }
        s1 = scorer.score(plan_no_fallback)
        s2 = scorer.score(plan_with_fallback)
        assert s2.robustness > s1.robustness

    def test_dependencies_reduce_simplicity(self):
        scorer = PlanScorer()
        plan_no_deps = {
            "id": "p1",
            "tasks": [{"id": "t1"}, {"id": "t2"}],
        }
        plan_with_deps = {
            "id": "p2",
            "tasks": [{"id": "t1"}, {"id": "t2"}],
            "dependencies": [["t1", "t2"]],
        }
        s1 = scorer.score(plan_no_deps)
        s2 = scorer.score(plan_with_deps)
        assert s1.simplicity >= s2.simplicity


# ─── PlanSimulator Tests ──────────────────────────────────


class TestPlanSimulator:
    def test_simulate_basic(self):
        sim = PlanSimulator()
        plan = {
            "id": "plan-1",
            "tasks": [
                {"id": "t1", "capability_id": "tool.python"},
                {"id": "t2", "capability_id": "tool.llm"},
            ],
        }
        result = sim.simulate(plan)
        assert isinstance(result, PlanSimulation)
        assert result.plan_id == "plan-1"
        assert result.predicted_confidence > 0
        assert result.predicted_cost_usd > 0
        assert result.predicted_duration_seconds > 0

    def test_simulate_empty_plan(self):
        sim = PlanSimulator()
        result = sim.simulate({"id": "empty", "tasks": []})
        assert result.predicted_success is False
        assert result.predicted_confidence == 0.0

    def test_simulate_missing_capability(self):
        sim = PlanSimulator()
        plan = {
            "id": "plan-1",
            "tasks": [{"id": "t1", "capability_id": "tool.nonexistent"}],
        }
        result = sim.simulate(plan, available_capabilities=["tool.python"])
        assert len(result.risk_factors) > 0
        assert "no disponible" in result.risk_factors[0]

    def test_simulate_all_available(self):
        sim = PlanSimulator()
        plan = {
            "id": "plan-1",
            "tasks": [
                {"id": "t1", "capability_id": "tool.python"},
                {"id": "t2", "capability_id": "tool.llm"},
            ],
        }
        result = sim.simulate(plan, available_capabilities=["tool.python", "tool.llm"])
        assert result.predicted_success is True
        assert result.predicted_confidence > 0.7

    def test_simulate_bottleneck_detection(self):
        sim = PlanSimulator()
        plan = {
            "id": "plan-1",
            "tasks": [
                {"id": "t1", "capability_id": "tool.python"},
                {"id": "t2", "capability_id": "tool.llm", "depends_on": ["t1", "t3"]},
                {"id": "t3", "capability_id": "tool.web"},
            ],
        }
        result = sim.simulate(plan)
        assert result.predicted_bottleneck_task == "t2"

    def test_simulate_cost_calculation(self):
        sim = PlanSimulator(costs={"tool.python": 0.01, "tool.llm": 0.05})
        plan = {
            "id": "plan-1",
            "tasks": [
                {"id": "t1", "capability_id": "tool.python"},
                {"id": "t2", "capability_id": "tool.llm"},
            ],
        }
        result = sim.simulate(plan)
        assert result.predicted_cost_usd == pytest.approx(0.06)


# ─── ObjectiveDecomposer Tests ────────────────────────────


class TestObjectiveDecomposer:
    def test_decompose_simple(self):
        decomposer = ObjectiveDecomposer()
        subs = decomposer.decompose("Generar informe de ventas")
        assert len(subs) >= 1
        assert subs[0].description == "Generar informe de ventas"

    def test_decompose_with_conjunction(self):
        decomposer = ObjectiveDecomposer()
        subs = decomposer.decompose("Generar PDF de ventas y enviarlo por email")
        # Debe dividirse en al menos 2 sub-objetivos
        leaves = [s for s in subs if s.is_leaf]
        assert len(leaves) >= 2

    def test_decompose_with_semicolons(self):
        decomposer = ObjectiveDecomposer()
        subs = decomposer.decompose("Buscar datos; Procesar datos; Generar informe")
        leaves = [s for s in subs if s.is_leaf]
        assert len(leaves) >= 3

    def test_decompose_max_depth(self):
        decomposer = ObjectiveDecomposer(max_depth=1)
        subs = decomposer.decompose("Generar PDF y enviar email")
        # Con max_depth=1, no debe haber recursión profunda
        max_depth = max(s.depth for s in subs)
        assert max_depth <= 2

    def test_decompose_no_split(self):
        decomposer = ObjectiveDecomposer()
        subs = decomposer.decompose("Una sola cosa")
        assert len(subs) == 1
        assert subs[0].is_leaf is True

    def test_decompose_has_parent_child(self):
        decomposer = ObjectiveDecomposer()
        subs = decomposer.decompose("Hacer A y Hacer B")
        # El primer elemento debe ser el root (no leaf)
        root = subs[0]
        assert root.is_leaf is False
        assert len(root.children) >= 2
        # Los hijos deben tener parent_id == root.id
        children = [s for s in subs if s.parent_id == root.id]
        assert len(children) >= 2

    def test_sub_objective_to_dict(self):
        sub = SubObjective(id="s1", description="test", depth=0)
        d = sub.to_dict()
        assert d["id"] == "s1"
        assert d["description"] == "test"


# ─── AutoReplanner Tests ──────────────────────────────────


class TestAutoReplanner:
    def test_replan_timeout(self):
        replanner = AutoReplanner()
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-1",
            failure_reason="Timeout after 30 seconds",
            original_plan_id="plan-1",
            attempt_number=1,
        )
        result = replanner.replan(ctx)
        assert result is not None
        assert result["should_replan"] is True
        assert "timeout" in result["reason"].lower()
        assert result["strategy"] == "retry_with_timeout_increase"
        assert result["attempt_number"] == 2

    def test_replan_max_attempts(self):
        replanner = AutoReplanner(max_attempts=2)
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-1",
            failure_reason="Some error",
            attempt_number=2,
        )
        result = replanner.replan(ctx)
        assert result["should_replan"] is False
        assert result["strategy"] == "abort"

    def test_replan_rate_limit(self):
        replanner = AutoReplanner()
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-1",
            failure_reason="Rate limit exceeded (429)",
            attempt_number=1,
        )
        result = replanner.replan(ctx)
        assert result["strategy"] == "retry_with_backoff"
        assert result["new_tasks"][0]["backoff_seconds"] > 0

    def test_replan_not_found(self):
        replanner = AutoReplanner()
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-1",
            failure_reason="Resource not found (404)",
            attempt_number=1,
            available_capabilities=["tool.alt1", "tool.alt2"],
        )
        result = replanner.replan(ctx)
        assert result["strategy"] == "alternative_capability"
        assert result["new_tasks"][0]["use_alternative"] is True

    def test_replan_unknown_error(self):
        replanner = AutoReplanner()
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-1",
            failure_reason="Something completely unexpected happened",
            attempt_number=1,
        )
        result = replanner.replan(ctx)
        assert result["strategy"] == "retry_with_modifications"
        assert result["should_replan"] is True

    def test_should_abort(self):
        replanner = AutoReplanner(max_attempts=3)
        ctx = ReplanContext(attempt_number=3)
        assert replanner.should_abort(ctx) is True

        ctx2 = ReplanContext(attempt_number=2)
        assert replanner.should_abort(ctx2) is False

    def test_replan_context_to_dict(self):
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-1",
            failure_reason="error",
            previous_errors=["error1"],
        )
        d = ctx.to_dict()
        assert d["execution_id"] == "exec-1"
        assert d["failed_task_id"] == "task-1"
        assert "error1" in d["previous_errors"]

    def test_replan_with_skill_suggestion(self, tmp_path):
        from eon.memory import Skill, SkillLibrary

        lib = SkillLibrary(db_path=str(tmp_path / "skills.db"))
        lib.register(
            Skill(
                name="alt_approach",
                objective_pattern="alternative approach",
                success_count=3,
            )
        )

        replanner = AutoReplanner(skill_library=lib)
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-1",
            failure_reason="Assertion error: expected output not found",
            attempt_number=1,
        )
        result = replanner.replan(ctx)
        assert result["strategy"] == "alternative_approach"
        lib.close()

    def test_modified_tasks_have_ids(self):
        replanner = AutoReplanner()
        ctx = ReplanContext(
            execution_id="exec-1",
            failed_task_id="task-1",
            failure_reason="Timeout",
            attempt_number=1,
        )
        result = replanner.replan(ctx)
        for task in result["new_tasks"]:
            assert "id" in task
            assert task["id"] != ""
