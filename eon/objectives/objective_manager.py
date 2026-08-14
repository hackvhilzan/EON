"""
eon.objectives.objective_manager
===================================
Toda la lógica del dominio de Objetivos. Único punto de escritura: nadie más
modifica un Objective directamente (OBJECTIVES.md §8, Invariante 6) -- ni
siquiera el ObjectiveStore, que solo persiste lo que aquí ya se validó.

Las operaciones expuestas son exactamente las de la tabla de §8, más dos
operaciones auxiliares (`desbloquear`, `iniciar`) que la propia máquina de
estados exige pero que §8 no asigna explícitamente a nadie -- ver su docstring
para la justificación de cada una. Esto no añade estados ni eventos nuevos al
contrato: solo nombra quién ejecuta transiciones que el diagrama de §5 ya
contemplaba.
"""

from __future__ import annotations

from typing import Any

from . import events, state_machine
from .dependency_graph import DependencyGraph
from .exceptions import (
    IllegalTransitionError,
    InvalidDecompositionError,
    ObjectiveNotFoundError,
    RetryLimitExceededError,
    TerminalObjectiveError,
)
from .models import Objective, ObjectiveOrigin, ObjectiveState
from .objective_store import ObjectiveStore
from .verifier import VerificationResult, Verifier


class ObjectiveManager:
    def __init__(
        self,
        store: ObjectiveStore,
        event_bus,
        verifier: Verifier | None = None,
        max_reintentos: int = 2,
    ):
        self._store = store
        self._events = event_bus
        self._graph = DependencyGraph(store)
        self._verifier = verifier or Verifier()
        self._max_reintentos = max_reintentos
        # §3: "Cada sub-objetivo declara si es obligatorio u opcional". No es un
        # campo de Objective (§1 no lo lista, y el modelo no admite campos extra)
        # -- es una relación padre->hijo que vive en quien gestiona la
        # descomposición, igual que `depende_de` vive en el hijo pero se valida
        # aquí. hijo_id -> obligatorio.
        self._obligatorio: dict[str, bool] = {}
        self._reintentos: dict[str, int] = {}

    # ---- utilidades internas ----

    def _require(self, objective_id: str) -> Objective:
        obj = self._store.get(objective_id)
        if obj is None:
            raise ObjectiveNotFoundError(f"Objetivo no encontrado: '{objective_id}'.")
        return obj

    def _transition(
        self, obj: Objective, destino: ObjectiveState, evento: str, motivo: str | None = None, **extra
    ) -> Objective:
        anterior = obj.estado
        obj.estado = destino
        obj.registrar_transicion(anterior.value, destino.value, evento, motivo=motivo, **extra)
        self._store.update(obj)
        self._events.emit(evento, objective_id=obj.id, de=anterior.value, a=destino.value, motivo=motivo, **extra)
        return obj

    # ---- crear_objetivo ----

    def crear_objetivo(
        self,
        descripcion: str,
        criterio_de_exito: str,
        origen: ObjectiveOrigin | str = ObjectiveOrigin.USUARIO,
        *,
        propietario: str | None = None,
        padre_id: str | None = None,
        depende_de: list[str] | None = None,
        confianza_minima: float = 0.7,
        valor: float = 0.5,
        reintento_de: str | None = None,
        motivo: str | None = None,
    ) -> Objective:
        """§2: nace en `pendiente`, nunca en `en_progreso`. `reintento_de` enlaza
        este Objetivo nuevo al que agotó reintentos, sin reabrirlo (§2 punto 3,
        Invariante 9) -- se anota en el historial, no es un campo del modelo."""
        depende_de = list(depende_de or [])
        origen = ObjectiveOrigin(origen) if isinstance(origen, str) else origen

        self._graph.validar_padre(padre_id)

        obj = Objective(
            descripcion=descripcion,
            criterio_de_exito=criterio_de_exito,
            origen=origen,
            propietario=propietario,
            padre_id=padre_id,
            depende_de=[],  # se rellena después de validar, para poder usar obj.id en la validación de ciclos
            confianza_minima=confianza_minima,
            valor=valor,
        )

        if depende_de:
            self._graph.validar_dependencias(obj.id, depende_de)
        obj.depende_de = depende_de

        obj.registrar_transicion(
            None, ObjectiveState.PENDIENTE.value, events.OBJETIVO_CREADO, motivo=motivo, reintento_de=reintento_de
        )
        self._store.create(obj)
        self._events.emit(
            events.OBJETIVO_CREADO,
            objective_id=obj.id,
            origen=origen.value,
            padre_id=padre_id,
            reintento_de=reintento_de,
            motivo=motivo,
        )
        return obj

    # ---- descomponer ----

    def descomponer(
        self, padre_id: str, sub_objetivos: list[dict[str, Any]], motivo: str | None = None
    ) -> list[Objective]:
        """§3: siempre crea hijos NUEVOS (nunca reparenta uno existente). Cada
        dict de `sub_objetivos` acepta: descripcion, criterio_de_exito (ambos
        obligatorios), y opcionalmente obligatorio (bool, default True),
        depende_de, confianza_minima, valor. No cambia el estado del padre."""
        padre = self._require(padre_id)
        if padre.estado.es_terminal:
            raise TerminalObjectiveError(f"No se puede descomponer un Objetivo terminal ('{padre.estado.value}').")
        if not sub_objetivos:
            raise InvalidDecompositionError("descomponer() necesita al menos un sub-objetivo.")

        hijos: list[Objective] = []
        for spec in sub_objetivos:
            if "descripcion" not in spec or "criterio_de_exito" not in spec:
                raise InvalidDecompositionError("Cada sub-objetivo necesita 'descripcion' y 'criterio_de_exito'.")
            hijo = self.crear_objetivo(
                descripcion=spec["descripcion"],
                criterio_de_exito=spec["criterio_de_exito"],
                origen=ObjectiveOrigin.DESCOMPOSICION,
                padre_id=padre_id,
                depende_de=spec.get("depende_de"),
                confianza_minima=spec.get("confianza_minima", 0.7),
                valor=spec.get("valor", 0.5),
                propietario=spec.get("propietario"),
            )
            self._obligatorio[hijo.id] = bool(spec.get("obligatorio", True))
            hijos.append(hijo)

        padre.registrar_evento(events.OBJETIVO_DESCOMPUESTO, motivo=motivo, hijos=[h.id for h in hijos])
        self._store.update(padre)
        self._events.emit(
            events.OBJETIVO_DESCOMPUESTO, objective_id=padre.id, hijos=[h.id for h in hijos], motivo=motivo
        )
        return hijos

    def es_obligatorio(self, hijo_id: str) -> bool:
        return self._obligatorio.get(hijo_id, True)

    # ---- planificar / desbloquear / iniciar ----

    def planificar(self, objective_id: str, motivo: str | None = None) -> Objective:
        """`pendiente -> planificando`, y si `depende_de` no está satisfecho,
        encadena inmediatamente a `bloqueado` (§5 diagrama: la arista
        `depende_de no cumplida` sale de `planificando`)."""
        obj = self._require(objective_id)
        evento = state_machine.validar_transicion(obj.estado, ObjectiveState.PLANIFICANDO)
        self._transition(obj, ObjectiveState.PLANIFICANDO, evento, motivo)

        if not self._graph.dependencias_cumplidas(obj):
            evento_bloqueo = state_machine.validar_transicion(obj.estado, ObjectiveState.BLOQUEADO)
            self._transition(obj, ObjectiveState.BLOQUEADO, evento_bloqueo, motivo="dependencia sin cumplir")
        return obj

    def desbloquear(self, objective_id: str, motivo: str | None = None) -> Objective:
        """`bloqueado -> planificando` cuando las dependencias ya se cumplieron
        (§5, evento `objetivo_desbloqueado`). §8 no asigna esta comprobación a
        ninguna de sus 8 operaciones con nombre -- "dependencias cumplidas" no
        tiene dueño explícito en el contrato -- pero la transición existe en la
        máquina de estados y algo debe poder dispararla; se expone aquí, en el
        único punto de escritura, en vez de dejarla implícita en otro sitio.
        Normalmente la llama quien supervisa las dependencias del Objetivo
        (Scheduler/Coordinator en el rol que corresponda) cuando una de ellas se
        completa."""
        obj = self._require(objective_id)
        if obj.estado != ObjectiveState.BLOQUEADO:
            raise IllegalTransitionError(f"Solo se puede desbloquear desde 'bloqueado' (actual: '{obj.estado.value}').")
        if not self._graph.dependencias_cumplidas(obj):
            raise IllegalTransitionError("Las dependencias de este Objetivo todavía no están completadas.")
        evento = state_machine.validar_transicion(obj.estado, ObjectiveState.PLANIFICANDO)
        return self._transition(obj, ObjectiveState.PLANIFICANDO, evento, motivo)

    def iniciar(self, objective_id: str, motivo: str = "plan listo") -> Objective:
        """`planificando -> en_progreso`. Fase 7 no genera el plan en sí (eso es
        del Planner, fuera de alcance de OBJECTIVES.md -- ver §0: Planner es un
        rol, no parte de este contrato) -- este método es el punto donde quien sí
        generó el plan le confirma al Objective Engine que puede empezar a
        ejecutarse."""
        obj = self._require(objective_id)
        evento = state_machine.validar_transicion(obj.estado, ObjectiveState.EN_PROGRESO)
        return self._transition(obj, ObjectiveState.EN_PROGRESO, evento, motivo)

    def obtener(self, objective_id: str) -> Objective:
        """Devuelve el Objetivo existente por id."""
        return self._require(objective_id)

    # ---- pausar / reanudar ----

    def pausar(self, objective_id: str, motivo: str | None = None) -> Objective:
        """§7: pausa recursivamente a los sub-objetivos no terminales. Nota de
        implementación: la máquina de estados (§5) solo dibuja `en_progreso ->
        pausado` como arista legal, pero §7 exige pausar el subárbol completo de
        no-terminales sin excepción -- incluidos hijos que estén en `pendiente`,
        `planificando` o `bloqueado`, estados desde los que `pausado` no es una
        arista dibujada para una llamada directa a `pausar()`. Se resuelve así:
        el Objetivo raíz de la llamada SÍ pasa por la validación estricta de la
        máquina de estados (solo se puede invocar `pausar()` sobre algo en
        `en_progreso`); la cascada hacia los descendientes es una consecuencia
        del padre pausándose, no una invocación independiente de `pausar()` por
        hijo, y por tanto no vuelve a pasar por esa misma arista -- cada
        descendiente no terminal queda marcado `pausado` con su propio historial
        e evento, tal como exige la Invariante 3 ("ninguna transición sin
        evento")."""
        obj = self._require(objective_id)
        evento = state_machine.validar_transicion(obj.estado, ObjectiveState.PAUSADO)
        self._transition(obj, ObjectiveState.PAUSADO, evento, motivo)

        for hijo in self._graph.descendientes(obj.id):
            if not hijo.estado.es_terminal and hijo.estado != ObjectiveState.PAUSADO:
                self._cascada(
                    hijo, ObjectiveState.PAUSADO, events.OBJETIVO_PAUSADO, motivo or "pausa en cascada del padre"
                )
        return obj

    def reanudar(self, objective_id: str, motivo: str | None = None) -> Objective:
        """§7: intenta volver a `en_progreso`; si en el ínterin dejó de cumplirse
        una dependencia, cae a `bloqueado` en vez de `en_progreso` directamente.
        A diferencia de `pausar`/`cancelar`, el contrato no dice que `reanudar`
        cascada a los hijos (§7 solo menciona la cascada para pausar, y §8 solo
        para cancelar) -- así que aquí NO se reanuda el subárbol automáticamente:
        cada Objetivo pausado se reanuda con su propia llamada explícita."""
        obj = self._require(objective_id)
        if obj.estado != ObjectiveState.PAUSADO:
            raise IllegalTransitionError(f"Solo se puede reanudar desde 'pausado' (actual: '{obj.estado.value}').")
        destino = ObjectiveState.EN_PROGRESO if self._graph.dependencias_cumplidas(obj) else ObjectiveState.BLOQUEADO
        evento = state_machine.validar_transicion(obj.estado, destino)
        return self._transition(obj, destino, evento, motivo)

    # ---- cancelar ----

    def cancelar(self, objective_id: str, motivo: str | None = None) -> Objective:
        """§8/Invariante 8: cancela recursivamente los sub-objetivos no
        terminales -- no se puede cancelar solo el padre y dejar un hijo
        ejecutando para una meta que ya no existe. Misma nota que en `pausar()`
        sobre por qué la cascada no reusa `validar_cancelacion` para cada hijo:
        `ESTADOS_CANCELABLES` no incluye `verificando`/`fallando`, pero un hijo
        transitoriamente en uno de esos dos estados en el momento en que su
        padre se cancela también debe terminar `cancelado`, no quedar huérfano
        de un padre que ya no existe."""
        obj = self._require(objective_id)
        evento = state_machine.validar_cancelacion(obj.estado)
        self._transition(obj, ObjectiveState.CANCELADO, evento, motivo)

        for hijo in self._graph.descendientes(obj.id):
            if not hijo.estado.es_terminal:
                self._cascada(
                    hijo,
                    ObjectiveState.CANCELADO,
                    events.OBJETIVO_CANCELADO,
                    motivo or "cancelación en cascada del padre",
                )
        return obj

    def _cascada(self, obj: Objective, destino: ObjectiveState, evento: str, motivo: str) -> Objective:
        anterior = obj.estado
        obj.estado = destino
        obj.registrar_transicion(anterior.value, destino.value, evento, motivo=motivo, cascada=True)
        self._store.update(obj)
        self._events.emit(evento, objective_id=obj.id, de=anterior.value, a=destino.value, motivo=motivo, cascada=True)
        return obj

    # ---- reasignar_propietario ----

    def reasignar_propietario(self, objective_id: str, nuevo_propietario: str, motivo: str | None = None) -> Objective:
        """§1/§8: cambia solo `propietario`. No cambia el estado ni el
        `criterio_de_exito`. Se anota en `historial` y emite `objetivo_reasignado`
        -- nunca un cambio silencioso de campo."""
        obj = self._require(objective_id)
        if obj.estado.es_terminal:
            raise TerminalObjectiveError(
                f"No se puede reasignar el propietario de un Objetivo terminal ('{obj.estado.value}')."
            )
        anterior = obj.propietario
        obj.propietario = nuevo_propietario
        obj.registrar_evento(
            events.OBJETIVO_REASIGNADO,
            motivo=motivo,
            propietario_anterior=anterior,
            propietario_nuevo=nuevo_propietario,
        )
        self._store.update(obj)
        self._events.emit(
            events.OBJETIVO_REASIGNADO,
            objective_id=obj.id,
            propietario_anterior=anterior,
            propietario_nuevo=nuevo_propietario,
            motivo=motivo,
        )
        return obj

    # ---- verificar ----

    def verificar(self, objective_id: str, resultado: Any = None, motivo: str | None = None) -> Objective:
        """§8/§9: única operación que puede mover un Objetivo a `completado`, y
        solo contra el `criterio_de_exito` fijado al crearlo. Cubre los tres
        casos de §9: dependencias no satisfechas, composición por sub-objetivos
        obligatorios, o verificación de un plan propio vía `Verifier`.

        Fase 4 (Unificación del contrato de verificación): `Verifier` es la
        única autoridad que decide un dictamen. Si `resultado` ya es un
        dictamen decidido (expone `.dictamen`, tal como lo entrega
        `eon.coordinator.ports.VerifierPort`), este método NO vuelve a
        invocar `self._verifier`: solo registra y aplica lo que ya se
        decidió. `self._verifier` solo se invoca cuando `resultado` es un
        dato crudo aún sin juzgar (uso directo de `ObjectiveManager` sin
        pasar por el Coordinator -- p.ej. con un `judge` inyectado, o un
        `(cumple, confianza)` ya evaluado a mano)."""
        obj = self._require(objective_id)
        evento = state_machine.validar_transicion(obj.estado, ObjectiveState.VERIFICANDO)
        self._transition(obj, ObjectiveState.VERIFICANDO, evento, motivo)

        # §9.3: dependencia cancelada/fallida en el ínterin -> no se completa.
        if self._graph.dependencia_fallida(obj):
            return self._fallar_verificacion(obj, "dependencia no satisfecha")

        hijos = self._store.children(obj.id)
        if hijos:
            # §9.1: se resuelve por composición de sub-objetivos, no por plan propio.
            obligatorios = [h for h in hijos if self.es_obligatorio(h.id)]
            if obligatorios and all(h.estado == ObjectiveState.COMPLETADO for h in obligatorios):
                return self._completar(obj)
            return self._fallar_verificacion(obj, "sub-objetivos obligatorios incompletos")

        # §9.2: se resuelve con un plan propio -- verificación contra criterio_de_exito.
        if self._es_dictamen_ya_decidido(resultado):
            resultado_verificacion = self._aplicar_dictamen_decidido(resultado)
        else:
            resultado_verificacion = self._verifier.verificar(obj, resultado)
        if resultado_verificacion.cumple:
            return self._completar(obj)
        return self._fallar_verificacion(obj, resultado_verificacion.motivo or "no cumple criterio_de_exito")

    @staticmethod
    def _es_dictamen_ya_decidido(resultado: Any) -> bool:
        """`True` si `resultado` ya es un dictamen decidido por un Verifier
        (expone `.dictamen`, VERIFIER.md §5.6/§2), en vez de un dato crudo
        pendiente de juzgar. Ni un `dict`, ni un `tuple`, ni `None` exponen
        `.dictamen` -- todos ellos siguen yendo por `self._verifier` como
        antes de la Fase 4, preservando el uso directo de `ObjectiveManager`
        sin Coordinator (con `judge` inyectado o un `(cumple, confianza)`
        ya evaluado a mano)."""
        return resultado is not None and hasattr(resultado, "dictamen")

    @staticmethod
    def _aplicar_dictamen_decidido(resultado: Any) -> VerificationResult:
        """Traduce un dictamen ya decidido (`dictamen`/`confianza`/
        `justificacion`) al `VerificationResult` interno (`cumple`/
        `confianza`/`motivo`) de `eon.objectives.verifier`, SIN volver a
        evaluar nada: la decisión ya la tomó el Verifier real antes de
        llegar aquí (vía Coordinator)."""
        cumple = resultado.dictamen == "aprobado"
        confianza = float(getattr(resultado, "confianza", 0.0) or 0.0)
        motivo = getattr(resultado, "justificacion", "") or (
            "" if cumple else "no cumple criterio_de_exito o confianza por debajo de confianza_minima"
        )
        return VerificationResult(cumple=cumple, confianza=confianza, motivo=motivo)

    def _completar(self, obj: Objective) -> Objective:
        evento = state_machine.validar_transicion(obj.estado, ObjectiveState.COMPLETADO)
        return self._transition(obj, ObjectiveState.COMPLETADO, evento, motivo="criterio_de_exito cumplido")

    def _fallar_verificacion(self, obj: Objective, motivo: str) -> Objective:
        evento = state_machine.validar_transicion(obj.estado, ObjectiveState.FALLANDO)
        return self._transition(obj, ObjectiveState.FALLANDO, evento, motivo=motivo)

    # ---- reintentar ----

    def reintentar(self, objective_id: str, motivo: str | None = None, max_reintentos: int | None = None) -> Objective:
        """§8: crea un plan nuevo para el MISMO Objetivo (no uno nuevo -- eso solo
        pasa si se agotan los reintentos, §2 punto 3, y es responsabilidad de
        quien orquesta el ciclo, no de esta llamada: aquí solo se decide si el
        Objetivo vuelve a `en_progreso` o cae a `fallido`). El límite de
        reintentos es una política del Objetivo (§11: "no se fija aquí cuántos,
        solo que exista un límite") -- por defecto el del manager, sobreescribible
        por llamada. Si el límite ya se agotó, el Objetivo igual transiciona a
        `fallido` (§5: la transición de dominio ocurre siempre) pero el método
        lo señaliza lanzando `RetryLimitExceededError` en vez de devolverlo con
        normalidad, para que quien orquesta el ciclo de reintentos (el
        Coordinator) reciba una señal explícita en vez de tener que releer el
        estado para distinguir "reintento concedido" de "reintentos
        agotados"."""
        obj = self._require(objective_id)
        limite = self._max_reintentos if max_reintentos is None else max_reintentos
        usados = self._reintentos.get(obj.id, 0)

        if usados >= limite:
            evento = state_machine.validar_transicion(obj.estado, ObjectiveState.FALLIDO)
            self._transition(obj, ObjectiveState.FALLIDO, evento, motivo or "reintentos agotados")
            raise RetryLimitExceededError(obj.id)

        evento = state_machine.validar_transicion(obj.estado, ObjectiveState.EN_PROGRESO)
        self._reintentos[obj.id] = usados + 1
        return self._transition(obj, ObjectiveState.EN_PROGRESO, evento, motivo or f"reintento {usados + 1}/{limite}")

    def reintentos_usados(self, objective_id: str) -> int:
        return self._reintentos.get(objective_id, 0)
