"""
eon.planning.scoring
=======================
PlanScorer: evalúa planes con criterios deterministas.

Métricas:
- efficiency: menos tasks = mejor (1/N normalizado)
- robustness: tasks con fallback = mejor
- coverage: tasks que cubren keywords del criterio
- simplicity: menos dependencias = mejor

El score total es un promedio ponderado.
"""

from __future__ import annotations

from typing import Any

from .models import PlanScore


class PlanScorer:
    """Scorer determinista de planes.

    No requiere LLM ni APIs externas.

    Uso:
        scorer = PlanScorer()
        score = scorer.score(
            plan={"id": "plan-1", "tasks": [...]},
            success_criteria="generar PDF de ventas",
        )
        print(score.total_score)  # 0..1
    """

    # Pesos de cada métrica
    WEIGHTS: dict[str, float] = {
        "efficiency": 0.25,
        "robustness": 0.20,
        "coverage": 0.30,
        "simplicity": 0.25,
    }

    def score(
        self,
        plan: dict[str, Any],
        success_criteria: str = "",
    ) -> PlanScore:
        """Evalúa un plan y devuelve su puntuación.

        Args:
            plan: Diccionario con "id", "tasks" (lista de dicts),
                  opcionalmente "dependencies" (lista de pares).
            success_criteria: Criterio de éxito del objetivo.
        """
        plan_id = plan.get("id", "")
        tasks = plan.get("tasks", [])
        dependencies = plan.get("dependencies", [])

        # ─── Efficiency ───
        # Menos tasks = más eficiente. 1 task = 1.0, 10+ tasks = ~0.1
        num_tasks = len(tasks)
        efficiency = max(0.1, 1.0 / num_tasks) if num_tasks > 0 else 0.0

        # ─── Robustness ───
        # Tasks con fallback/capacidad alternativa = más robusto
        tasks_with_fallback = sum(
            1 for t in tasks if isinstance(t, dict) and (t.get("fallback") or t.get("alternatives"))
        )
        robustness = tasks_with_fallback / num_tasks if num_tasks > 0 else 0.0
        # Si no hay fallbacks pero hay pocas tasks, robust base de 0.3
        if robustness == 0.0:
            robustness = 0.3

        # ─── Coverage ───
        # Keywords del criterio cubiertas por descriptions de tasks
        criteria_words = {
            w.lower()
            for w in success_criteria.split()
            if len(w) > 3  # ignorar stopwords simples
        }
        covered = 0
        if criteria_words and tasks:
            task_texts = []
            for t in tasks:
                if isinstance(t, dict):
                    task_texts.append((t.get("description", "") + " " + t.get("capability_id", "")).lower())
                elif isinstance(t, str):
                    task_texts.append(t.lower())
            covered = sum(1 for word in criteria_words if any(word in tt for tt in task_texts))
            coverage = covered / len(criteria_words) if criteria_words else 0.5
        else:
            coverage = 0.5

        # ─── Simplicity ───
        # Menos dependencias = más simple
        num_deps = len(dependencies)
        if num_tasks > 0:
            dep_ratio = num_deps / (num_tasks * (num_tasks - 1) / 2) if num_tasks > 1 else 0.0
            simplicity = max(0.0, 1.0 - dep_ratio)
        else:
            simplicity = 1.0

        # ─── Total ───
        total = (
            efficiency * self.WEIGHTS["efficiency"]
            + robustness * self.WEIGHTS["robustness"]
            + coverage * self.WEIGHTS["coverage"]
            + simplicity * self.WEIGHTS["simplicity"]
        )

        return PlanScore(
            plan_id=plan_id,
            total_score=round(total, 4),
            efficiency=round(efficiency, 4),
            robustness=round(robustness, 4),
            coverage=round(coverage, 4),
            simplicity=round(simplicity, 4),
            details={
                "num_tasks": num_tasks,
                "num_dependencies": num_deps,
                "tasks_with_fallback": tasks_with_fallback,
                "criteria_words_matched": covered if criteria_words else 0,
            },
        )

    def rank(
        self,
        plans: list[dict[str, Any]],
        success_criteria: str = "",
    ) -> list[PlanScore]:
        """Evalúa múltiples planes y los ordena por score descendente."""
        scores = [self.score(p, success_criteria) for p in plans]
        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores
