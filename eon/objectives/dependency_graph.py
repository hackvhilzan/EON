"""
eon.objectives.dependency_graph
==================================
Gestiona las dos relaciones distintas que puede tener un Objetivo (OBJECTIVES.md
§4): `padre_id` (composición, árbol) y `depende_de` (orden de ejecución, no
necesariamente familiar). Valida ciclos y forma de árbol.

No duplica estado: no mantiene su propia copia del grafo, lee siempre del
ObjectiveStore que se le inyecta -- igual que CapabilityGraph no mantiene su
propia copia del CapabilityRegistry. Es un conjunto de recorridos y validaciones
puras sobre datos que ya viven en el store.
"""

from __future__ import annotations

from .exceptions import CyclicDependencyError, InvalidHierarchyError, ObjectiveNotFoundError
from .models import Objective, ObjectiveState


class DependencyGraph:
    def __init__(self, store):
        self._store = store

    # ---- padre / hijos (composición, árbol -- Invariante 10) ----

    def hijos(self, objective_id: str) -> list[Objective]:
        return self._store.children(objective_id)

    def raices(self) -> list[Objective]:
        return self._store.root_objectives()

    def ancestros(self, objective_id: str) -> list[Objective]:
        """Camino hacia la raíz siguiendo `padre_id`. Se detiene si detecta que ya
        pasó por este id -- eso indicaría un ciclo colado, que nunca debería
        ocurrir si `validar_padre` se aplicó siempre al crear (defensivo, no
        normativo)."""
        resultado: list[Objective] = []
        visto = {objective_id}
        actual = self._store.get(objective_id)
        while actual is not None and actual.padre_id is not None:
            if actual.padre_id in visto:
                break
            padre = self._store.get(actual.padre_id)
            if padre is None:
                break
            resultado.append(padre)
            visto.add(padre.id)
            actual = padre
        return resultado

    def descendientes(self, objective_id: str) -> list[Objective]:
        """Todo el subárbol (recursivo), no solo hijos directos. Usado por
        pausar()/cancelar() en cascada (§7, §8)."""
        resultado: list[Objective] = []
        for hijo in self.hijos(objective_id):
            resultado.append(hijo)
            resultado.extend(self.descendientes(hijo.id))
        return resultado

    def validar_padre(self, padre_id: str | None) -> None:
        """Se llama ANTES de crear un Objetivo con ese `padre_id`. Como
        `descomponer` siempre crea hijos nuevos (§3) y nunca reparenta uno
        existente, el único ciclo posible en la práctica es apuntar a un
        `padre_id` que no existe; se rechaza aquí igualmente cualquier intento
        futuro de reparentar (Invariante 10)."""
        if padre_id is None:
            return
        if self._store.get(padre_id) is None:
            raise ObjectiveNotFoundError(f"padre_id apunta a un Objetivo inexistente: '{padre_id}'.")

    def validar_reparentado(self, objective_id: str, nuevo_padre_id: str) -> None:
        """Este contrato no admite reparentar (§3, Invariante 10) -- este método
        existe solo para dejar explícito, si alguna vez se intenta, por qué se
        rechaza: crearía potencialmente un ciclo (el nuevo padre podría ser un
        descendiente del propio Objetivo) además de violar la inmutabilidad de
        `padre_id` (§1)."""
        if objective_id == nuevo_padre_id:
            raise InvalidHierarchyError("Un Objetivo no puede ser su propio padre.")
        descendientes_ids = {d.id for d in self.descendientes(objective_id)}
        if nuevo_padre_id in descendientes_ids:
            raise CyclicDependencyError(
                f"Reparentar '{objective_id}' bajo '{nuevo_padre_id}' crearía un ciclo: "
                f"'{nuevo_padre_id}' es descendiente de '{objective_id}'."
            )
        raise InvalidHierarchyError("Reparentar no es una operación de este contrato (OBJECTIVES.md §3).")

    # ---- depende_de (orden de ejecución -- Invariante 5) ----

    def validar_dependencias(self, nuevo_id: str, depende_de: list[str]) -> None:
        """Se llama ANTES de crear un Objetivo con esas dependencias. Rechaza
        referencias a Objetivos inexistentes y cualquier ciclo -- directo
        (`nuevo_id` depende de sí mismo) o transitivo (una de las dependencias
        depende, directa o indirectamente, del propio `nuevo_id`)."""
        for dep_id in depende_de:
            if dep_id == nuevo_id:
                raise CyclicDependencyError(f"Un Objetivo no puede depender de sí mismo ('{nuevo_id}').")
            if self._store.get(dep_id) is None:
                raise ObjectiveNotFoundError(f"depende_de apunta a un Objetivo inexistente: '{dep_id}'.")

        for dep_id in depende_de:
            visitados: set[str] = set()
            pila = [dep_id]
            while pila:
                actual_id = pila.pop()
                if actual_id == nuevo_id:
                    raise CyclicDependencyError(
                        f"Dependencia circular detectada: '{nuevo_id}' depende (transitivamente) de sí mismo "
                        f"vía '{dep_id}'."
                    )
                if actual_id in visitados:
                    continue
                visitados.add(actual_id)
                actual = self._store.get(actual_id)
                if actual is not None:
                    pila.extend(actual.depende_de)

    def dependencias_cumplidas(self, objective: Objective) -> bool:
        """§4/§9.3: todas las `depende_de` deben estar `completado`."""
        for dep_id in objective.depende_de:
            dep = self._store.get(dep_id)
            if dep is None or dep.estado != ObjectiveState.COMPLETADO:
                return False
        return True

    def dependencia_fallida(self, objective: Objective) -> bool:
        """§9.3: si alguna dependencia se canceló o falló, este Objetivo no puede
        completarse -- pasa a `fallando` con motivo "dependencia no satisfecha",
        no se cuela como completado a medias."""
        for dep_id in objective.depende_de:
            dep = self._store.get(dep_id)
            if dep is not None and dep.estado in (ObjectiveState.CANCELADO, ObjectiveState.FALLIDO):
                return True
        return False
