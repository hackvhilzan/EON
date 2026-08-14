"""
eon.planning.models
======================
Modelos de datos para planificación inteligente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanScore:
    """Puntuación de un plan por el PlanScorer."""

    plan_id: str = ""
    total_score: float = 0.0  # 0..1, mayor = mejor
    efficiency: float = 0.0  # menos tasks/deps = mejor
    robustness: float = 0.5  # con fallbacks = mejor
    coverage: float = 0.5  # cubre el criterio de éxito
    simplicity: float = 0.5  # menos dependencias = mejor
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "total_score": self.total_score,
            "efficiency": self.efficiency,
            "robustness": self.robustness,
            "coverage": self.coverage,
            "simplicity": self.simplicity,
            "details": self.details,
        }


@dataclass
class PlanSimulation:
    """Resultado de simular un plan con PlanSimulator."""

    plan_id: str = ""
    predicted_success: bool = False
    predicted_confidence: float = 0.0
    predicted_duration_seconds: float = 0.0
    predicted_cost_usd: float = 0.0
    predicted_bottleneck_task: str = ""
    risk_factors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "predicted_success": self.predicted_success,
            "predicted_confidence": self.predicted_confidence,
            "predicted_duration_seconds": self.predicted_duration_seconds,
            "predicted_cost_usd": self.predicted_cost_usd,
            "predicted_bottleneck_task": self.predicted_bottleneck_task,
            "risk_factors": self.risk_factors,
            "details": self.details,
        }


@dataclass
class SubObjective:
    """Sub-objetivo generado por ObjectiveDecomposer."""

    id: str = ""
    description: str = ""
    parent_id: str = ""
    depth: int = 0
    estimated_tasks: int = 1
    is_leaf: bool = True
    children: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "estimated_tasks": self.estimated_tasks,
            "is_leaf": self.is_leaf,
            "children": list(self.children),
        }


@dataclass
class ReplanContext:
    """Contexto para una replanificación por AutoReplanner."""

    execution_id: str = ""
    failed_task_id: str = ""
    failure_reason: str = ""
    original_plan_id: str = ""
    attempt_number: int = 1
    previous_errors: list[str] = field(default_factory=list)
    partial_results: dict[str, Any] = field(default_factory=dict)
    available_capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "failed_task_id": self.failed_task_id,
            "failure_reason": self.failure_reason,
            "original_plan_id": self.original_plan_id,
            "attempt_number": self.attempt_number,
            "previous_errors": list(self.previous_errors),
            "partial_results": dict(self.partial_results),
            "available_capabilities": list(self.available_capabilities),
        }
