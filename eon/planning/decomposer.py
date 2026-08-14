"""
eon.planning.decomposer
=========================
ObjectiveDecomposer: descompone objetivos complejos en sub-objetivos.

Usa descomposición heurística:
- "y" / "además" → divide en múltiples sub-objetivos
- Pasos implícitos ("X y luego Y") → secuencia
- max_depth para prevenir loops infinitos
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from .models import SubObjective


class ObjectiveDecomposer:
    """Descompone objetivos complejos en sub-objetivos.

    Uso:
        decomposer = ObjectiveDecomposer(max_depth=3)
        subs = decomposer.decompose("Generar PDF de ventas y enviarlo por email")
        # [SubObjective("generar PDF"), SubObjective("enviar por email")]
    """

    # Delimitadores de sub-objetivos
    SPLITS: list[str] = [
        r"\s+y\s+",
        r"\s+además\s+",
        r"\s+después\s+",
        r"\s+luego\s+",
        r"\s+finalmente\s+",
        r";\s*",
        r"\.\s+",
    ]

    # Palabras que indican pasos secuenciales
    SEQUENTIAL_MARKERS: list[str] = [
        "luego", "después", "finalmente", "a continuación",
    ]

    def __init__(self, max_depth: int = 3) -> None:
        self._max_depth = max_depth

    def decompose(
        self,
        objective: str,
        parent_id: str = "",
        depth: int = 0,
    ) -> list[SubObjective]:
        """Descompone un objetivo en sub-objetivos.

        Args:
            objective: Descripción del objetivo.
            parent_id: ID del objetivo padre (para recursión).
            depth: Profundidad actual.

        Returns:
            Lista de sub-objetivos. Si no se puede descomponer,
            devuelve una lista con un solo sub-objetivo (el original).
        """
        if depth >= self._max_depth:
            return [SubObjective(
                id=str(uuid.uuid4()),
                description=objective.strip(),
                parent_id=parent_id,
                depth=depth,
                estimated_tasks=1,
                is_leaf=True,
            )]

        # Intentar dividir por delimitadores
        parts = self._split_objective(objective)

        if len(parts) <= 1:
            # No se puede descomponer más
            return [SubObjective(
                id=str(uuid.uuid4()),
                description=objective.strip(),
                parent_id=parent_id,
                depth=depth,
                estimated_tasks=1,
                is_leaf=True,
            )]

        # Crear sub-objetivos
        sub_objectives: list[SubObjective] = []
        parent_for_children = str(uuid.uuid4())

        # Sub-objetivo padre (agrupador)
        root = SubObjective(
            id=parent_for_children,
            description=objective.strip(),
            parent_id=parent_id,
            depth=depth,
            estimated_tasks=len(parts),
            is_leaf=False,
        )

        children_ids: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Verificar si este sub-objetivo puede descomponerse más
            sub_parts = self._split_objective(part)
            can_decompose = len(sub_parts) > 1

            child_id = str(uuid.uuid4())
            children_ids.append(child_id)

            sub = SubObjective(
                id=child_id,
                description=part,
                parent_id=parent_for_children,
                depth=depth + 1,
                estimated_tasks=1,
                is_leaf=not can_decompose,
            )
            sub_objectives.append(sub)

            # Recursión si puede descomponerse
            if can_decompose and depth + 1 < self._max_depth:
                grandchildren = self.decompose(
                    part, parent_id=child_id, depth=depth + 2
                )
                sub_objectives.extend(grandchildren)
                sub.is_leaf = False
                sub.children = [g.id for g in grandchildren if g.parent_id == child_id]

        root.children = children_ids
        result = [root] + sub_objectives
        return result

    def _split_objective(self, objective: str) -> list[str]:
        """Divide un objetivo por delimitadores heurísticos."""
        parts = [objective]

        for pattern in self.SPLITS:
            new_parts: list[str] = []
            for part in parts:
                splits = re.split(pattern, part, flags=re.IGNORECASE)
                new_parts.extend(splits)
            parts = new_parts

        # Filtrar partes vacías
        return [p.strip() for p in parts if p.strip()]
