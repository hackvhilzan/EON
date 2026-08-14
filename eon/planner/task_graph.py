"""
eon.planner.task_graph
=========================
Deriva el orden total de las Tasks de un Plan (PLANNER.md Invariante 8) a
partir de sus dependencias declaradas (`Task.depende_de`, ver task.py).

No mantiene estado propio -- opera siempre sobre la lista de Tasks que ya
vive dentro de un Plan concreto, igual que DependencyGraph
(eon.objectives.dependency_graph) opera sobre lo que ya vive en el
ObjectiveStore en vez de duplicarlo.

`depende_de` en Task es de orden (qué debe ir antes), no de composición --
equivalente a `depende_de` en Objective (OBJECTIVES.md §4), nunca a
`padre_id`: una Task no tiene sub-Tasks, PLANNER.md no contempla jerarquía
dentro de un Plan.
"""

from __future__ import annotations

from .planner_exceptions import InvalidTaskGraphError
from .task import Task


class TaskGraph:
    def __init__(self, tasks: list[Task]):
        self._tasks = list(tasks)
        self._por_id = {t.id: t for t in self._tasks}

    # ---- validación (Invariante 8: el orden total debe poder derivarse sin ambigüedad) ----

    def validar(self) -> None:
        """Rechaza referencias a Tasks que no pertenecen a este mismo Plan y
        cualquier ciclo entre Tasks -- un ciclo haría imposible un orden
        total, igual que OBJECTIVES.md rechaza ciclos en `depende_de` al
        declararse (§4), nunca en tiempo de ejecución."""
        for tarea in self._tasks:
            for dep_id in tarea.depende_de:
                if dep_id not in self._por_id:
                    raise InvalidTaskGraphError(
                        f"La Task '{tarea.id}' depende de '{dep_id}', que no pertenece a este Plan "
                        "(PLANNER.md Invariante 5: las Tasks pertenecen a un único Plan)."
                    )

        visitando: set[str] = set()
        visitado: set[str] = set()

        def _dfs(tarea_id: str) -> None:
            if tarea_id in visitado:
                return
            if tarea_id in visitando:
                raise InvalidTaskGraphError(
                    f"Ciclo de dependencias entre Tasks detectado en '{tarea_id}' "
                    "(PLANNER.md Invariante 8: las Tasks poseen un orden total)."
                )
            visitando.add(tarea_id)
            for dep_id in self._por_id[tarea_id].depende_de:
                _dfs(dep_id)
            visitando.discard(tarea_id)
            visitado.add(tarea_id)

        for tarea in self._tasks:
            _dfs(tarea.id)

    # ---- orden total ----

    def orden_total(self) -> list[Task]:
        """Orden topológico determinista: entre Tasks sin relación de
        dependencia entre sí, se conserva el orden de aparición en la lista
        original -- para que el mismo Plan siempre produzca el mismo orden
        (PLANNER.md Invariante 15: un Plan es completamente reproducible a
        partir de su contenido)."""
        self.validar()
        resultado: list[Task] = []
        colocado: set[str] = set()

        def _colocar(tarea: Task) -> None:
            if tarea.id in colocado:
                return
            for dep_id in tarea.depende_de:
                _colocar(self._por_id[dep_id])
            resultado.append(tarea)
            colocado.add(tarea.id)

        for tarea in self._tasks:
            _colocar(tarea)
        return resultado
