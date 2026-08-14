"""
eon.planner.planner
======================
Toda la lógica de dominio de Plan. Único punto de escritura: nadie más
modifica un Plan directamente (PLANNER.md §1, Inmutabilidad -- mismo criterio
que ObjectiveManager para Objective).

Las operaciones expuestas cubren exactamente los tres momentos de nacimiento
de §2 (`crear_plan`, `replanificar`, `reintentar`), la generación y selección
de estrategias alternativas de §5 (`generar_candidatos`, `activar`), y las
dos reglas de cierre añadidas al congelar el contrato: el descarte automático
de candidatos no elegidos, y la cancelación en cascada desde el Objetivo
asociado (`cancelar_por_objetivo`).

PlannerManager no conoce ObjectiveManager ni lo importa (PLANNER.md §0: "no
conoce infraestructura", y Objective/Planner son dominios separados). Quien
orquesta ambos -- Core, en fases posteriores -- es quien decide cuándo llamar
`iniciar()` sobre el Objetivo tras `activar()` un Plan, y cuándo llamar
`cancelar_por_objetivo()` aquí tras `cancelar()` un Objetivo.
"""

from __future__ import annotations

from . import plan_selector, validators
from . import planner_events as events
from .models import Plan, PlanOrigin, PlanState
from .planner_exceptions import InvalidPlanError, PlanNotFoundError
from .planner_store import PlannerStore
from .task import Task


class PlannerManager:
    def __init__(self, store: PlannerStore, event_bus):
        self._store = store
        self._events = event_bus

    # ---- utilidades internas ----

    def _require(self, plan_id: str) -> Plan:
        plan = self._store.get(plan_id)
        if plan is None:
            raise PlanNotFoundError(f"Plan no encontrado: '{plan_id}'.")
        return plan

    def _siguiente_version(self, objective_id: str) -> int:
        """PLANNER.md §1 (regla de cierre): `version` se incrementa en cada
        Plan nuevo del Objetivo, sin importar el origen (planificación
        inicial, replanificación o reintento)."""
        existentes = self._store.by_objective(objective_id)
        if not existentes:
            return 1
        return max(p.version for p in existentes) + 1

    def _transition(self, plan: Plan, destino: PlanState, evento: str, motivo: str | None = None, **extra) -> Plan:
        anterior = plan.estado
        plan.estado = destino
        plan.registrar_transicion(anterior.value, destino.value, evento, motivo=motivo, **extra)
        self._store.update(plan)
        self._events.emit(
            evento,
            plan_id=plan.id,
            objective_id=plan.objective_id,
            de=anterior.value,
            a=destino.value,
            motivo=motivo,
            **extra,
        )
        return plan

    def _nacer(
        self,
        objective_id: str,
        tasks: list[Task],
        origen: PlanOrigin,
        metadata: dict | None = None,
        motivo: str | None = None,
    ) -> Plan:
        """§2: los tres momentos de nacimiento comparten la misma mecánica --
        nacen en `creado`, nunca implícitamente, nunca ya `activo`. Lo único
        que cambia entre ellos es la versión (siempre incrementada) y el
        evento (`plan_creado` salvo replanificación, que emite
        `plan_replanificado` -- §9, EVENTOS_DE_NACIMIENTO son mutuamente
        excluyentes)."""
        plan = Plan(
            objective_id=objective_id,
            tasks=tasks,
            version=self._siguiente_version(objective_id),
            metadata=dict(metadata or {}),
        )
        evento = events.PLAN_REPLANIFICADO if origen == PlanOrigin.REPLANIFICACION else events.PLAN_CREADO
        plan.registrar_transicion(None, PlanState.CREADO.value, evento, motivo=motivo, origen=origen.value)
        self._store.create(plan)
        self._events.emit(evento, plan_id=plan.id, objective_id=objective_id, origen=origen.value, motivo=motivo)
        return plan

    # ---- nacimiento: los tres momentos de §2 ----

    def crear_plan(
        self, objective_id: str, tasks: list[Task], metadata: dict | None = None, motivo: str | None = None
    ) -> Plan:
        """§2, situación 1: planificación inicial de un Objetivo nuevo."""
        return self._nacer(objective_id, tasks, PlanOrigin.PLANIFICACION_INICIAL, metadata, motivo)

    def replanificar(
        self, objective_id: str, tasks: list[Task], metadata: dict | None = None, motivo: str | None = None
    ) -> Plan:
        """§2, situación 2; §7: replanificar nunca modifica el Plan existente,
        siempre crea una versión nueva. Emite `plan_replanificado`."""
        return self._nacer(objective_id, tasks, PlanOrigin.REPLANIFICACION, metadata, motivo)

    def reintentar(
        self, objective_id: str, tasks: list[Task], metadata: dict | None = None, motivo: str | None = None
    ) -> Plan:
        """§2, situación 3: tras un reintento del Objetivo. Emite
        `plan_creado`, no `plan_replanificado` -- §9: desde la perspectiva del
        dominio del Plan sigue siendo una creación nueva."""
        return self._nacer(objective_id, tasks, PlanOrigin.REINTENTO, metadata, motivo)

    # ---- estrategias alternativas (§5) ----

    def generar_candidatos(
        self,
        objective_id: str,
        tasks_por_candidato: list[list[Task]],
        metadata_por_candidato: list[dict] | None = None,
        motivo: str | None = None,
    ) -> list[Plan]:
        """§5: el Planner puede generar varios planes candidatos. Ninguno nace
        activo -- todos nacen en `creado`, cada uno con su propia versión
        (regla de cierre de §1). Elegir uno es un paso posterior explícito
        (`activar`), nunca implícito en la generación."""
        if not tasks_por_candidato:
            raise InvalidPlanError("generar_candidatos necesita al menos una lista de Tasks candidata (PLANNER.md §5).")
        metadatas = metadata_por_candidato or [{} for _ in tasks_por_candidato]
        if len(metadatas) != len(tasks_por_candidato):
            raise InvalidPlanError("metadata_por_candidato debe tener la misma longitud que tasks_por_candidato.")
        return [
            self.crear_plan(objective_id, tasks, metadata=meta, motivo=motivo)
            for tasks, meta in zip(tasks_por_candidato, metadatas)
        ]

    # ---- activación (§5, §8) ----

    def activar(self, plan_id: str, motivo: str | None = None) -> Plan:
        """§5/§8: activa este Plan. En el mismo acto:
        1. el Plan previamente activo del mismo Objetivo (si existe) pasa a
           `obsoleto` (§8, arista normal `activo -> obsoleto`);
        2. cualquier otro candidato del mismo Objetivo que siga en `creado`
           (generado por `generar_candidatos` pero no elegido) pasa
           directamente a `obsoleto` (§8, regla de cierre).
        Nunca quedan dos Planes activos para el mismo Objetivo (Invariante 2)."""
        plan = self._require(plan_id)
        evento = validators.validar_transicion(plan.estado, PlanState.ACTIVO)

        anterior_activo = self._store.active_for_objective(plan.objective_id)
        if anterior_activo is not None and anterior_activo.id != plan.id:
            evento_obsoleto = validators.validar_transicion(anterior_activo.estado, PlanState.OBSOLETO)
            self._transition(
                anterior_activo, PlanState.OBSOLETO, evento_obsoleto, motivo="reemplazado por un nuevo Plan activo"
            )

        self._transition(plan, PlanState.ACTIVO, evento, motivo)

        for candidato in self._store.by_objective(plan.objective_id):
            if candidato.id == plan.id:
                continue
            if candidato.estado == PlanState.CREADO:
                evento_descarte = validators.validar_descarte_candidato(candidato.estado)
                self._transition(candidato, PlanState.OBSOLETO, evento_descarte, motivo="candidato no elegido")

        return plan

    def elegir_y_activar(
        self,
        candidatos: list[Plan],
        criterio: plan_selector.CriterioSeleccion = plan_selector.primero,
        motivo: str | None = None,
    ) -> Plan:
        """Conecta `plan_selector` (selección pura) con `activar` (la única
        operación que escribe): aplica `criterio` sobre `candidatos` --
        normalmente el resultado de `generar_candidatos` -- y activa el
        elegido, descartando el resto (ver `activar`)."""
        elegido = plan_selector.seleccionar(candidatos, criterio)
        return self.activar(elegido.id, motivo=motivo)

    # ---- cancelación (§8) ----

    def cancelar(self, plan_id: str, motivo: str | None = None) -> Plan:
        """§8 (regla de cierre): cancelable desde `creado`, `activo` u
        `obsoleto`. Un Plan cancelado nunca vuelve atrás."""
        plan = self._require(plan_id)
        evento = validators.validar_cancelacion(plan.estado)
        return self._transition(plan, PlanState.CANCELADO, evento, motivo)

    def cancelar_por_objetivo(self, objective_id: str, motivo: str | None = None) -> Plan | None:
        """§8 (regla de cierre): cuando el Objetivo asociado se cancela
        (`OBJECTIVES.md` §8, operación `cancelar`), su Plan activo pasa
        automáticamente a `cancelado` en el mismo acto. Quien orquesta la
        cancelación del Objetivo (Core, en fases posteriores) es quien invoca
        este método -- PlannerManager no escucha eventos de Objective por su
        cuenta (§0: el Planner no conoce Objetivos más allá de leer su
        `objective_id`). Devuelve `None` si el Objetivo no tenía Plan activo
        (nunca se planificó, o ya estaba obsoleto/cancelado)."""
        activo = self._store.active_for_objective(objective_id)
        if activo is None:
            return None
        return self.cancelar(activo.id, motivo=motivo or "Objetivo asociado cancelado")

    # ---- lectura ----

    def obtener(self, plan_id: str) -> Plan:
        return self._require(plan_id)

    def activo_de(self, objective_id: str) -> Plan | None:
        return self._store.active_for_objective(objective_id)

    def historial_de(self, objective_id: str) -> list[Plan]:
        """Todos los Planes del Objetivo, en orden de versión -- incluye el
        activo, los obsoletos y los cancelados (§3: los anteriores permanecen
        como versiones anteriores, accesibles pero no activas)."""
        return sorted(self._store.by_objective(objective_id), key=lambda p: p.version)
