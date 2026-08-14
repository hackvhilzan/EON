"""
eon.coordinator.ports
========================
El Coordinator "Únicamente podrá invocar sus interfaces públicas"
(ORDEN MAESTRA, "AISLAMIENTO") de Objectives, Planner, Scheduler, Workers,
Verifier, Workspace y Package. Este paquete de entrega (Fase 13) no incluye
el código fuente de esos módulos -- el `.zip` recibido como contexto solo
contenía `eon/package/` (Fase 12) -- por lo que, para no inventar ni
modificar ningún archivo existente ("IMPORTANTE: No modificar ningún
archivo existente"), el Coordinator se implementa contra **Protocols**
(`typing.Protocol`) que fijan exactamente la interfaz pública descrita en
cada contrato (`OBJECTIVES.md` §8, `PLANNER.md` §8, `SCHEDULER.md` §9,
`VERIFIER.md` §9, `WORKSPACE.md` §4.3/§12, `PACKAGE.md` §4.2 y el código
real de `PackageManager`).

Esto es una decisión de API explícita (ver informe de la Fase 13), no una
reinterpretación de ningún contrato: cada método de cada Protocol cita la
sección exacta que lo define. Cuando `eon/objectives/`, `eon/planner/`,
`eon/scheduler/`, `eon/verifier/` y `eon/workspace/` existan como código,
sus Managers concretos satisfacen estos Protocols de forma estructural
(duck typing) sin que este archivo cambie una sola línea -- o, si sus
firmas reales difieren, el ajuste es aquí, nunca dentro de `manager.py`,
que solo conoce estos Protocols.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ObjetivoRef(Protocol):
    """Vista de solo lectura de un Objetivo, tal como la necesita el
    Coordinator (OBJECTIVES.md §1)."""

    id: str
    estado: str


@runtime_checkable
class ObjectiveDescriptorRef(Protocol):
    """Vista de solo lectura de un Objetivo con lo mínimo que
    `TaskGenerationPort` necesita para generar su especificación de trabajo
    (OBJECTIVES.md §1: `descripcion` y `criterio_de_exito` existen desde que
    el Objetivo nace). Es un superconjunto de `ObjetivoRef` -- el mismo
    objeto que devuelve `ObjectivesPort.obtener` lo satisface por duck
    typing, sin que el Coordinator tenga que construir nada nuevo."""

    id: str
    descripcion: str
    criterio_de_exito: str
    estado: str


@runtime_checkable
class TaskSpecRef(Protocol):
    """Vista de solo lectura de un `TaskSpec` (`eon.task_generation.models`),
    tal como la necesita `PlannerPort` para construir el Plan. Mismos cuatro
    campos que `eon.planner.task.Task`, porque describe lo mismo desde un
    punto anterior del flujo (Fase 2, Task Generation)."""

    id: str
    capability_id: str
    depende_de: tuple[str, ...]
    parametros: dict


@runtime_checkable
class PlanRef(Protocol):
    """Vista de solo lectura de un Plan (PLANNER.md §1)."""

    id: str
    objective_id: str
    version: int
    estado: str


@runtime_checkable
class VerificationResultRef(Protocol):
    """Vista de solo lectura de un `VerificationResult` (VERIFIER.md §5.6).
    `dictamen` toma uno de los tres valores de VERIFIER.md §2:
    `"aprobado"`, `"rechazado"`, `"requiere_reintento"`."""

    dictamen: str
    confianza: float
    justificacion: str


@runtime_checkable
class WorkspaceRefPort(Protocol):
    """Vista de solo lectura de un Workspace -- mismo espíritu que
    `eon.package.models.WorkspaceRef` (PACKAGE.md §0.1), reutilizada aquí
    porque el Coordinator necesita exactamente los mismos cuatro campos
    para poder, a su vez, delegar en `PackagePort.solicitar`."""

    workspace_id: str
    objective_id: str
    estado: str
    artifacts_path: str


@runtime_checkable
class PackageRef(Protocol):
    """Vista de solo lectura de un Package (PACKAGE.md §1)."""

    id: str
    workspace_id: str
    estado: str


class ObjectivesPort(Protocol):
    """Subconjunto de OBJECTIVES.md §8 ("¿Quién puede modificarlo?") que el
    Coordinator tiene autorización para invocar. El resto de operaciones de
    §8 (`pausar`, `reanudar`, `descomponer`, `reasignar_propietario`) no
    forman parte de las responsabilidades listadas en la ORDEN MAESTRA de
    la Fase 13 y por tanto no se exponen aquí, aunque el contrato de
    Objectives las permita a otros roles."""

    def crear_objetivo(self, descripcion: str, criterio_de_exito: str, **kwargs: Any) -> ObjetivoRef:
        """OBJECTIVES.md §8, `crear_objetivo`. Responsabilidad 1 de la
        ORDEN MAESTRA ("Crear un Objetivo")."""
        ...

    def planificar(self, objective_id: str, motivo: str | None = None) -> ObjetivoRef:
        """`pendiente -> planificando` (OBJECTIVES.md §5). El Coordinator debe
        avisar al Objective Manager antes de iniciar el Objetivo y crear el Plan."""
        ...

    def iniciar(self, objective_id: str) -> ObjetivoRef:
        """`planificando -> en_progreso` (OBJECTIVES.md §5), presupuesto
        por `PLANNER.md` §8: "La activación del Plan precede inmediatamente
        a `Objetivo.iniciar()`" y "el Planner nunca llama `iniciar()`
        directamente -- eso sigue siendo responsabilidad de Core". El
        Coordinator, como orquestador del Kernel, es quien la invoca tras
        activar el Plan."""
        ...

    def obtener(self, objective_id: str) -> ObjetivoRef:
        """Obtiene el Objetivo por su id para que el Coordinator pueda
        consultar el estado y el criterio de éxito."""
        ...

    def verificar(self, objective_id: str, resultado: VerificationResultRef) -> ObjetivoRef:
        """OBJECTIVES.md §8, `verificar`: decide `completado`/`fallando`
        según `criterio_de_exito` y `confianza_minima`, a partir del
        dictamen ya emitido por el Verifier (nunca lo decide el
        Coordinator, que aquí solo transporta el dictamen)."""
        ...

    def reintentar(self, objective_id: str) -> ObjetivoRef:
        """OBJECTIVES.md §8, `reintentar`. Lanza `RetriesExhaustedError`
        (ver `exceptions.py`) si la política de reintentos del Objetivo ya
        se agotó -- el límite es responsabilidad de Objectives
        (OBJECTIVES.md §11), nunca del Coordinator."""
        ...

    def cancelar(self, objective_id: str, motivo: str) -> ObjetivoRef:
        """OBJECTIVES.md §8, `cancelar`."""
        ...


class PlannerPort(Protocol):
    """PLANNER.md §8 (tabla de eventos/operaciones) y §2 ("¿Cuándo nace?").

    Fase 2 (Task Generation): el Coordinator ya no decide ni construye las
    Tasks -- las recibe de `TaskGenerationPort` como `TaskSpec[]` y se
    limita a transportarlas hasta aquí. La conversión `TaskSpec -> Task`
    (el tipo real que exige `PlannerManager.crear_plan`/`replanificar`) es
    responsabilidad del adaptador concreto que implementa este Port, nunca
    del Coordinator."""

    def planificar(self, objective_id: str, tasks: Sequence[TaskSpecRef]) -> PlanRef:
        """Crea y activa el primer Plan de un Objetivo (PLANNER.md §2,
        situación 1), a partir de la especificación de Tasks ya generada.
        Responsabilidad 2 de la ORDEN MAESTRA ("Solicitar el Plan")."""
        ...

    def replanificar(self, objective_id: str, motivo: str, tasks: Sequence[TaskSpecRef]) -> PlanRef:
        """Crea una nueva versión de Plan y la activa, dejando la anterior
        `obsoleto` en el mismo acto (PLANNER.md §5, §7), a partir de una
        nueva especificación de Tasks. Responsabilidad 6 de la ORDEN
        MAESTRA ("Gestionar replanificaciones cuando el contrato lo
        permita")."""
        ...


class TaskGenerationPort(Protocol):
    """Fase 2 (Task Generation). El Coordinator "NO puede... decidir cómo
    se generan las tareas": este Port es el único punto por el que el
    Coordinator obtiene la especificación de trabajo de un Objetivo, antes
    de solicitar el Plan al Planner --

        Objective -> TaskGenerationPort -> TaskSpec[] -> Planner -> Plan

    Ningún método de `TaskGenerationPort` construye un `eon.planner.task.Task`
    ni ningún objeto de `eon.planner`: solo `TaskSpec` (`eon.task_generation`).
    """

    def generar(self, objective: ObjectiveDescriptorRef) -> Sequence[TaskSpecRef]:
        """Genera la especificación inicial de Tasks para `objective`. Debe
        devolver al menos un `TaskSpec`; el Coordinator no decide ni valida
        cómo se llegó a esa especificación, solo la transporta al
        Planner."""
        ...


class SchedulerPort(Protocol):
    """SCHEDULER.md §9 (API pública), subconjunto que el Coordinator
    delega. El Coordinator nunca llama `marcar_running`/`marcar_completed`/
    `marcar_failed` (SCHEDULER.md §9.7-9.9): esas transiciones las
    disparan los Workers, no el Coordinator (ORDEN MAESTRA: "NO ejecuta
    Workers")."""

    def crear(self, plan_id: str, **kwargs: Any) -> str:
        """SCHEDULER.md §9.1, `crear`. Devuelve el `scheduler_id`. Decisión
        de API (ver informe): el Coordinator no persiste `scheduler_id`
        como campo propio -- fuera de la lista cerrada de "ESTADO INTERNO"
        de la ORDEN MAESTRA -- por lo que este Protocol exige que el
        adaptador real use `plan_id` como `scheduler_id`, o lo resuelva
        internamente sin exponer un identificador adicional al
        Coordinator."""
        ...

    def iniciar(self, scheduler_id: str) -> None:
        """SCHEDULER.md §9.2. Responsabilidad 3 de la ORDEN MAESTRA
        ("Lanzar el Scheduler")."""
        ...

    def cancelar(self, scheduler_id: str, motivo: str) -> None:
        """SCHEDULER.md §9.5."""
        ...


class VerifierPort(Protocol):
    """VERIFIER.md §9 (API pública)."""

    def verificar(self, evidence: Any, criterio: str, umbral: float) -> VerificationResultRef:
        """VERIFIER.md §9.1. Responsabilidad 5 de la ORDEN MAESTRA
        ("Lanzar el Verifier")."""
        ...


class WorkspacePort(Protocol):
    """WORKSPACE.md §4.3 (WorkspaceManager) y §3 (máquina de estados). El
    Coordinator es "el único invocador autorizado de las transiciones de
    §3" (WORKSPACE.md §12)."""

    def crear(self, objective_id: str, configuracion: dict | None = None) -> str:
        """WORKSPACE.md §2: "el Coordinator crea el Workspace como paso
        previo". Devuelve `workspace_id`, en `CREATED`."""
        ...

    def planificando(self, workspace_id: str) -> None:
        """`CREATED -> PLANNING` / `VERIFYING -> PLANNING` (WORKSPACE.md §3)."""
        ...

    def scheduling(self, workspace_id: str) -> None:
        """`PLANNING -> SCHEDULING` (WORKSPACE.md §3)."""
        ...

    def ejecutando(self, workspace_id: str) -> None:
        """`SCHEDULING -> RUNNING` (WORKSPACE.md §3)."""
        ...

    def verificando(self, workspace_id: str) -> None:
        """`RUNNING -> VERIFYING` (WORKSPACE.md §3)."""
        ...

    def completar(self, workspace_id: str) -> None:
        """`VERIFYING -> COMPLETED` (WORKSPACE.md §3)."""
        ...

    def fallar(self, workspace_id: str, motivo: str) -> None:
        """`VERIFYING -> FAILED` / `RUNNING -> FAILED` (WORKSPACE.md §3)."""
        ...

    def cancelar(self, workspace_id: str, motivo: str) -> None:
        """Cualquier no-terminal `-> CANCELLED` (WORKSPACE.md §3)."""
        ...

    def obtener(self, workspace_id: str) -> WorkspaceRefPort:
        """Necesario para construir el `WorkspaceRef` que exige
        `PackagePort.solicitar` (PACKAGE.md §0.1)."""
        ...


class PackagePort(Protocol):
    """PACKAGE.md §4.2 (`PackageManager`), firmas tomadas directamente del
    código real de `eon/package/package.py` entregado en la Fase 12."""

    def solicitar(
        self,
        workspace_ref: WorkspaceRefPort,
        configuracion: dict | None = None,
        package_id: str | None = None,
    ) -> PackageRef:
        """`PackageManager.solicitar` -- crea el Package en `PENDING`."""
        ...

    def construir(self, package_id: str, workspace_ref: WorkspaceRefPort) -> PackageRef:
        """`PackageManager.construir` -- `PENDING -> BUILDING -> READY|FAILED`.
        Responsabilidad 8 de la ORDEN MAESTRA ("Solicitar el Package")."""
        ...


class EventBusPort(Protocol):
    """Mismo `EventBus` compartido que usan todos los módulos del Kernel
    (`event_bus.emit(...)` en `PackageManager`). El Coordinator añade
    `subscribe` porque, a diferencia de Package, "toda la coordinación
    será exclusivamente mediante EventBus" (ORDEN MAESTRA, "EVENTOS"): el
    Coordinator reacciona a eventos, no solo los emite."""

    def emit(self, event: str, **kwargs: Any) -> None: ...

    def subscribe(self, event: str, handler: Any) -> None: ...
