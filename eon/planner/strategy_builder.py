"""
eon.planner.strategy_builder
===============================
Ensambla una lista ordenada de Task a partir de especificaciones en bruto
(dicts) -- el paso "¿cuál es la mejor estrategia?" de PLANNER.md §0, pero
puramente estructural: este módulo no decide contenido, no habla con ningún
proveedor LLM ni con Tools (§0, §11 -- "Proveedores LLM" está fuera de
alcance del contrato). Quien decide QUÉ Tasks hacen falta es quien llama a
`construir()` -- humano, heurística o LLM real en una fase posterior; este
módulo solo garantiza que el resultado sea un candidato válido de Plan:
Tasks bien formadas (task.py) con un orden total derivable (task_graph.py).

Separado de PlannerManager (igual que ObjectiveManager delega en
DependencyGraph) para que la construcción de una estrategia se pueda probar y
reutilizar sin pasar por el ciclo de vida completo de un Plan.
"""

from __future__ import annotations

from typing import Any

from .planner_exceptions import InvalidPlanError
from .task import Task
from .task_graph import TaskGraph


def construir(especificaciones: list[dict[str, Any]]) -> list[Task]:
    """Cada spec acepta: `capability_id` (obligatorio), y opcionalmente
    `id`, `depende_de` (lista de ids -- deben referirse a otras Tasks de la
    misma lista de especificaciones) y `parametros`. Devuelve las Tasks en su
    orden total (PLANNER.md Invariante 8), no en el orden en que se
    especificaron -- ver `TaskGraph.orden_total`."""
    if not especificaciones:
        raise InvalidPlanError("Una estrategia necesita al menos una especificación de Task (PLANNER.md §1).")

    tareas: list[Task] = []
    for spec in especificaciones:
        if "capability_id" not in spec:
            raise InvalidPlanError("Cada especificación de Task necesita 'capability_id' (PLANNER.md §4).")
        kwargs: dict[str, Any] = {
            "capability_id": spec["capability_id"],
            "depende_de": tuple(spec.get("depende_de") or ()),
            "parametros": spec.get("parametros") or {},
        }
        if spec.get("id"):
            kwargs["id"] = spec["id"]
        tareas.append(Task(**kwargs))

    grafo = TaskGraph(tareas)
    return grafo.orden_total()


def construir_candidatos(especificaciones_por_candidato: list[list[dict[str, Any]]]) -> list[list[Task]]:
    """Azúcar para generar varias estrategias candidatas de una sola vez
    (PLANNER.md §5) -- cada elemento de la lista externa es una estrategia
    completa e independiente, validada por separado."""
    if not especificaciones_por_candidato:
        raise InvalidPlanError("generar_candidatos necesita al menos una estrategia candidata (PLANNER.md §5).")
    return [construir(specs) for specs in especificaciones_por_candidato]
