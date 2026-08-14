"""
eon.planning.simulator
========================
PlanSimulator: predice éxito/coste/tiempo de un plan.

Dry-run: no ejecuta tasks realmente, solo predice.
Usa datos históricos si están disponibles (EpisodicMemoryStore opcional).
Identifica bottlenecks (tasks con muchas dependencias).
"""
from __future__ import annotations

import logging
from typing import Any

from .models import PlanSimulation

logger = logging.getLogger("eon.planning.simulator")


class PlanSimulator:
    """Simula un plan en dry-run para predecir resultado.

    Uso:
        sim = PlanSimulator()
        result = sim.simulate(
            plan={"id": "plan-1", "tasks": [...]},
        )
        print(result.predicted_success, result.predicted_confidence)
    """

    # Costes por defecto por capability (estimados)
    DEFAULT_COSTS: dict[str, float] = {
        "tool.python": 0.01,
        "tool.llm": 0.05,
        "tool.web": 0.02,
        "tool.pdf": 0.01,
    }

    DEFAULT_DURATIONS: dict[str, float] = {
        "tool.python": 2.0,
        "tool.llm": 5.0,
        "tool.web": 3.0,
        "tool.pdf": 1.0,
    }

    def __init__(
        self,
        history_store: Any | None = None,
        costs: dict[str, float] | None = None,
        durations: dict[str, float] | None = None,
    ) -> None:
        self._history = history_store
        self._costs = costs or dict(self.DEFAULT_COSTS)
        self._durations = durations or dict(self.DEFAULT_DURATIONS)

    def simulate(
        self,
        plan: dict[str, Any],
        available_capabilities: list[str] | None = None,
    ) -> PlanSimulation:
        """Simula la ejecución de un plan.

        Args:
            plan: Diccionario con "id", "tasks" (lista de dicts).
            available_capabilities: Lista de capabilities disponibles.

        Returns:
            PlanSimulation con predicciones.
        """
        plan_id = plan.get("id", "")
        tasks = plan.get("tasks", [])
        check_availability = available_capabilities is not None
        available = set(available_capabilities or [])

        risk_factors: list[str] = []
        total_cost = 0.0
        total_duration = 0.0
        bottleneck_task = ""
        max_deps = 0

        # Capacidades no disponibles = riesgo
        for task in tasks:
            if not isinstance(task, dict):
                continue

            cap_id = task.get("capability_id", "tool.python")
            task_id = task.get("id", "unknown")

            # Verificar disponibilidad
            if check_availability and cap_id not in available:
                risk_factors.append(f"Capability '{cap_id}' no disponible para task '{task_id}'")

            # Coste estimado
            total_cost += self._costs.get(cap_id, 0.02)

            # Duración estimada
            total_duration += self._durations.get(cap_id, 3.0)

            # Bottleneck: task con más dependencias
            deps = task.get("depends_on", [])
            if isinstance(deps, list) and len(deps) > max_deps:
                max_deps = len(deps)
                bottleneck_task = task_id

        # Confidence basado en risks
        num_tasks = len(tasks)
        missing_caps = [r for r in risk_factors if "no disponible" in r]
        if num_tasks == 0:
            confidence = 0.0
            predicted_success = False
        elif missing_caps:
            # Capabilities faltantes = fallo seguro
            confidence = 0.0
            predicted_success = False
        elif risk_factors:
            # Otros risk factors penalizan
            penalty = len(risk_factors) * 0.15
            confidence = max(0.1, 0.9 - penalty)
            predicted_success = confidence > 0.5
        else:
            confidence = 0.85
            predicted_success = True

        # Consultar histórico si disponible
        if self._history:
            try:
                similar = self._history.search_similar(
                    plan.get("description", ""),
                    limit=3,
                    only_successful=True,
                )
                if similar:
                    # Ajustar confidence basado en históricos exitosos
                    historical_boost = min(0.1, len(similar) * 0.03)
                    confidence = min(1.0, confidence + historical_boost)
            except Exception:
                pass  # Fallar silenciosamente si el histórico no funciona

        if bottleneck_task:
            risk_factors.append(f"Bottleneck: task '{bottleneck_task}' con {max_deps} dependencias")

        return PlanSimulation(
            plan_id=plan_id,
            predicted_success=predicted_success,
            predicted_confidence=round(confidence, 4),
            predicted_duration_seconds=round(total_duration, 2),
            predicted_cost_usd=round(total_cost, 4),
            predicted_bottleneck_task=bottleneck_task,
            risk_factors=risk_factors,
            details={
                "num_tasks": num_tasks,
                "max_dependencies": max_deps,
            },
        )
