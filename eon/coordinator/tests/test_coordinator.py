"""Tests del Coordinator (Fase 13). Auto-contenidos: usan dobles de prueba
(Fakes) que satisfacen `eon.coordinator.ports` por duck typing, sin
importar ni tocar `eon.objectives`, `eon.planner`, `eon.scheduler`,
`eon.verifier`, `eon.workspace` ni `eon.package` -- mismo criterio que
`eon/package/tests/test_package.py` (Fase 12).

Ejecutable con la librería estándar (`python3 -m unittest`), sin
dependencias externas.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
import unittest
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eon.coordinator import (
    CoordinatorImmutableError,
    CoordinatorManager,
    CoordinatorNotFoundError,
    CoordinatorRecoveryError,
    CoordinatorState,
    FileCoordinatorStore,
    IllegalCoordinatorTransitionError,
    InMemoryCoordinatorStore,
    RetriesExhaustedError,
    events,
)
from eon.coordinator.validators import validar_transicion


# ---------------------------------------------------------------------------
# EventBus mínimo, síncrono (mismo patrón que test_package.py).
# ---------------------------------------------------------------------------
class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list] = {}
        self.emitidos: list[tuple[str, dict]] = []

    def subscribe(self, event: str, handler) -> None:
        self._subs.setdefault(event, []).append(handler)

    def emit(self, event: str, **kwargs) -> None:
        self.emitidos.append((event, kwargs))
        for handler in list(self._subs.get(event, [])):
            handler(**kwargs)


# ---------------------------------------------------------------------------
# Dobles de prueba para cada Port (ports.py).
# ---------------------------------------------------------------------------
@dataclass
class FakeObjetivo:
    id: str
    estado: str
    criterio_de_exito: str
    confianza_minima: float
    descripcion: str = "Objetivo de prueba"


class FakeObjectives:
    def __init__(self, max_reintentos: int = 3) -> None:
        self._store: dict[str, FakeObjetivo] = {}
        self._reintentos: dict[str, int] = {}
        self.max_reintentos = max_reintentos
        self.cancelados: list[str] = []
        # Fase 5 (bugfix verificar->reintentar): registro de llamadas, en
        # orden, para que los tests puedan comprobar que el Coordinator
        # siempre aplica `verificar` antes de `reintentar` y que nunca
        # vuelve a verificar por su cuenta (responsabilidad exclusiva del
        # Verifier -- el Coordinator solo transporta el dictamen).
        self.orden_llamadas: list[tuple[str, str]] = []

    def crear_objetivo(self, descripcion, criterio_de_exito, **kwargs):
        oid = str(uuid.uuid4())
        obj = FakeObjetivo(
            id=oid,
            estado="pendiente",
            criterio_de_exito=criterio_de_exito,
            confianza_minima=kwargs.get("confianza_minima", 0.8),
        )
        self._store[oid] = obj
        return obj

    def planificar(self, objective_id, motivo=None):
        obj = self._store[objective_id]
        obj.estado = "planificando"
        return obj

    def iniciar(self, objective_id):
        obj = self._store[objective_id]
        obj.estado = "en_progreso"
        return obj

    def obtener(self, objective_id):
        return self._store[objective_id]

    def verificar(self, objective_id, resultado):
        self.orden_llamadas.append(("verificar", objective_id))
        obj = self._store[objective_id]
        obj.estado = "completado" if resultado.dictamen == "aprobado" else "fallando"
        return obj

    def reintentar(self, objective_id):
        self.orden_llamadas.append(("reintentar", objective_id))
        n = self._reintentos.get(objective_id, 0) + 1
        self._reintentos[objective_id] = n
        if n > self.max_reintentos:
            raise RetriesExhaustedError(objective_id)
        self._store[objective_id].estado = "en_progreso"
        return self._store[objective_id]

    def cancelar(self, objective_id, motivo):
        self.cancelados.append(objective_id)
        obj = self._store[objective_id]
        obj.estado = "cancelado"
        return obj


@dataclass
class FakePlan:
    id: str
    objective_id: str
    version: int
    estado: str = "activo"


class FakePlanner:
    """Fase 2 (Task Generation): `planificar`/`replanificar` reciben ahora
    los `TaskSpec` ya generados -- este Fake no los usa (no valida nada de
    Planner en TestFlujoCompleto/TestAislamiento), solo comprueba que le
    llegan por firma."""

    def __init__(self) -> None:
        self._versiones: dict[str, int] = {}
        self.tasks_recibidas: list[Any] = []

    def planificar(self, objective_id, tasks):
        self._versiones[objective_id] = 1
        self.tasks_recibidas.append(tasks)
        return FakePlan(id=str(uuid.uuid4()), objective_id=objective_id, version=1)

    def replanificar(self, objective_id, motivo, tasks):
        v = self._versiones.get(objective_id, 1) + 1
        self._versiones[objective_id] = v
        self.tasks_recibidas.append(tasks)
        return FakePlan(id=str(uuid.uuid4()), objective_id=objective_id, version=v)


@dataclass(frozen=True)
class FakeTaskSpec:
    id: str
    capability_id: str = "default"
    depende_de: tuple = ()
    parametros: dict = field(default_factory=dict)


class FakeTaskGeneration:
    """Doble de prueba de `TaskGenerationPort`: siempre genera exactamente
    un `TaskSpec` determinista a partir del `objective` recibido, sin
    depender de `eon.task_generation` (mismo criterio de aislamiento que el
    resto de Fakes de este archivo)."""

    def __init__(self) -> None:
        self.objetivos_recibidos: list[Any] = []

    def generar(self, objective):
        self.objetivos_recibidos.append(objective)
        return [FakeTaskSpec(id=str(uuid.uuid4()), parametros={"objective_id": objective.id})]


class FakeScheduler:
    def __init__(self) -> None:
        self.creados: list[str] = []
        self.iniciados: list[str] = []
        self.cancelados: list[str] = []

    def crear(self, plan_id, **kwargs):
        self.creados.append(plan_id)
        return plan_id  # decisión de API: scheduler_id == plan_id

    def iniciar(self, scheduler_id):
        self.iniciados.append(scheduler_id)

    def cancelar(self, scheduler_id, motivo):
        self.cancelados.append(scheduler_id)


@dataclass
class FakeVerificationResult:
    dictamen: str
    confianza: float
    justificacion: str = ""


class FakeVerifier:
    def __init__(self, resultados: list[FakeVerificationResult] | None = None) -> None:
        self._cola = list(resultados) if resultados else [FakeVerificationResult("aprobado", 1.0)]
        self.llamadas = 0

    def verificar(self, evidence, criterio, umbral):
        self.llamadas += 1
        if len(self._cola) > 1:
            return self._cola.pop(0)
        return self._cola[0]


@dataclass
class FakeWorkspaceRef:
    workspace_id: str
    objective_id: str
    estado: str
    artifacts_path: str


class FakeWorkspace:
    def __init__(self, event_bus: EventBus) -> None:
        self._events = event_bus
        self._estado: dict[str, str] = {}
        self._objective: dict[str, str] = {}
        self.cancelados: list[str] = []

    def crear(self, objective_id, configuracion=None):
        wid = str(uuid.uuid4())
        self._estado[wid] = "CREATED"
        self._objective[wid] = objective_id
        return wid

    def planificando(self, workspace_id):
        self._estado[workspace_id] = "PLANNING"

    def scheduling(self, workspace_id):
        self._estado[workspace_id] = "SCHEDULING"

    def ejecutando(self, workspace_id):
        self._estado[workspace_id] = "RUNNING"

    def verificando(self, workspace_id):
        self._estado[workspace_id] = "VERIFYING"

    def completar(self, workspace_id):
        self._estado[workspace_id] = "COMPLETED"
        self._events.emit(events.WORKSPACE_COMPLETADO, workspace_id=workspace_id)

    def fallar(self, workspace_id, motivo):
        self._estado[workspace_id] = "FAILED"
        self._events.emit(events.WORKSPACE_FALLIDO, workspace_id=workspace_id, motivo=motivo)

    def cancelar(self, workspace_id, motivo):
        self._estado[workspace_id] = "CANCELLED"
        self.cancelados.append(workspace_id)

    def obtener(self, workspace_id):
        return FakeWorkspaceRef(
            workspace_id=workspace_id,
            objective_id=self._objective[workspace_id],
            estado=self._estado[workspace_id],
            artifacts_path=f"/fake/{workspace_id}/artifacts",
        )


@dataclass
class FakePackageRef:
    id: str
    workspace_id: str
    estado: str


class FakePackage:
    def __init__(self, estado_final: str = "ready") -> None:
        self.estado_final = estado_final
        self.solicitados: list[str] = []
        self.construidos: list[str] = []

    def solicitar(self, workspace_ref, configuracion=None, package_id=None):
        pid = package_id or str(uuid.uuid4())
        self.solicitados.append(pid)
        return FakePackageRef(id=pid, workspace_id=workspace_ref.workspace_id, estado="pending")

    def construir(self, package_id, workspace_ref):
        self.construidos.append(package_id)
        return FakePackageRef(id=package_id, workspace_id=workspace_ref.workspace_id, estado=self.estado_final)


# ---------------------------------------------------------------------------
def construir_manager(root: str, store=None, package_estado="ready", verifier_resultados=None, max_reintentos=3):
    event_bus = EventBus()
    objectives = FakeObjectives(max_reintentos=max_reintentos)
    planner = FakePlanner()
    scheduler = FakeScheduler()
    verifier = FakeVerifier(verifier_resultados)
    workspace = FakeWorkspace(event_bus)
    package = FakePackage(estado_final=package_estado)
    task_generation = FakeTaskGeneration()
    manager = CoordinatorManager(
        root=root,
        store=store or InMemoryCoordinatorStore(),
        event_bus=event_bus,
        objectives=objectives,
        planner=planner,
        scheduler=scheduler,
        verifier=verifier,
        workspace=workspace,
        package=package,
        task_generation=task_generation,
    )
    return manager, dict(
        event_bus=event_bus,
        objectives=objectives,
        planner=planner,
        scheduler=scheduler,
        verifier=verifier,
        workspace=workspace,
        package=package,
        task_generation=task_generation,
    )


class TestFlujoCompleto(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_camino_feliz_de_extremo_a_extremo(self):
        manager, deps = construir_manager(self.tmp)
        execution = manager.iniciar_ejecucion("Crea un SaaS de clínicas", "El SaaS despliega y pasa los tests")

        self.assertEqual(execution.estado, CoordinatorState.RUNNING)
        self.assertIsNotNone(execution.objective_id)
        self.assertIsNotNone(execution.plan_id)
        self.assertIsNotNone(execution.workspace_id)
        self.assertIsNone(execution.package_id)

        # Simula que el Scheduler terminó de despachar todas las Tasks
        # (evento asíncrono, SCHEDULER.md §10).
        deps["event_bus"].emit(events.SCHEDULER_FINALIZADO, scheduler_id=execution.plan_id, evidence={"ok": True})

        final = manager.obtener(execution.id)
        self.assertEqual(final.estado, CoordinatorState.COMPLETED)
        self.assertIsNotNone(final.package_id)
        self.assertEqual(deps["verifier"].llamadas, 1)
        self.assertEqual(deps["objectives"].obtener(final.objective_id).estado, "completado")

        # Ninguna transición silenciosa: cada entrada de historial trae
        # evento, de, a y cuando.
        for entrada in final.historial:
            for campo in ("evento", "de", "a", "cuando"):
                self.assertIn(campo, entrada)

        secuencia = [(h["de"], h["a"]) for h in final.historial]
        self.assertIn((None, "created"), secuencia)
        self.assertIn(("created", "creating_objective"), secuencia)
        self.assertIn(("packaging", "completed"), secuencia)

    def test_replanificacion_tras_rechazo_y_luego_aprobacion(self):
        manager, deps = construir_manager(
            self.tmp,
            verifier_resultados=[
                FakeVerificationResult("rechazado", 0.2, "faltan tests"),
                FakeVerificationResult("aprobado", 0.95),
            ],
        )
        execution = manager.iniciar_ejecucion("Objetivo X", "Criterio X")
        plan_v1 = execution.plan_id

        deps["event_bus"].emit(events.SCHEDULER_FINALIZADO, scheduler_id=plan_v1, evidence={})

        tras_rechazo = manager.obtener(execution.id)
        self.assertEqual(tras_rechazo.estado, CoordinatorState.RUNNING)
        self.assertNotEqual(tras_rechazo.plan_id, plan_v1)
        self.assertEqual(deps["planner"]._versiones[tras_rechazo.objective_id], 2)

        # Fase 5 (bugfix): tras el rechazo, Objectives.verificar() se
        # invocó ANTES que Objectives.reintentar() -- nunca al revés, y
        # nunca se omite (Test A/B/D del plan de Fase 5).
        objective_id = tras_rechazo.objective_id
        llamadas_del_objetivo = [(op, oid) for op, oid in deps["objectives"].orden_llamadas if oid == objective_id]
        self.assertEqual(llamadas_del_objetivo, [("verificar", objective_id), ("reintentar", objective_id)])
        # El Coordinator nunca vuelve a verificar por su cuenta: una sola
        # llamada al Verifier por cada scheduler_finalizado recibido hasta
        # ahora (Test D).
        self.assertEqual(deps["verifier"].llamadas, 1)

        deps["event_bus"].emit(events.SCHEDULER_FINALIZADO, scheduler_id=tras_rechazo.plan_id, evidence={})
        final = manager.obtener(execution.id)
        self.assertEqual(final.estado, CoordinatorState.COMPLETED)
        self.assertEqual(deps["verifier"].llamadas, 2)
        # La segunda vez el dictamen es "aprobado": Objectives.verificar()
        # se llama (para completar el Objetivo), pero NO Objectives.reintentar().
        llamadas_del_objetivo = [(op, oid) for op, oid in deps["objectives"].orden_llamadas if oid == objective_id]
        self.assertEqual(
            llamadas_del_objetivo,
            [("verificar", objective_id), ("reintentar", objective_id), ("verificar", objective_id)],
        )

    def test_reintentos_agotados_termina_en_fallido(self):
        manager, deps = construir_manager(
            self.tmp,
            verifier_resultados=[FakeVerificationResult("rechazado", 0.1, "no cumple")],
            max_reintentos=0,
        )
        execution = manager.iniciar_ejecucion("Objetivo Y", "Criterio Y")
        deps["event_bus"].emit(events.SCHEDULER_FINALIZADO, scheduler_id=execution.plan_id, evidence={})
        final = manager.obtener(execution.id)
        self.assertEqual(final.estado, CoordinatorState.FAILED)
        self.assertEqual(deps["workspace"]._estado[final.workspace_id], "FAILED")
        self.assertIsNone(final.package_id)
        # Test C: el Objective recibió el VerificationResult (pasó por
        # "fallando" -- observable en el Fake como estado final) antes de
        # que Objectives.reintentar() señalizara el agotamiento.
        self.assertEqual(deps["objectives"].obtener(final.objective_id).estado, "fallando")
        objective_id = final.objective_id
        llamadas_del_objetivo = [(op, oid) for op, oid in deps["objectives"].orden_llamadas if oid == objective_id]
        self.assertEqual(llamadas_del_objetivo, [("verificar", objective_id), ("reintentar", objective_id)])

    def test_evento_de_scheduler_desconocido_es_ignorado(self):
        manager, deps = construir_manager(self.tmp)
        execution = manager.iniciar_ejecucion("Objetivo Z", "Criterio Z")
        # No debe lanzar ni alterar la ejecución existente.
        deps["event_bus"].emit(events.SCHEDULER_FINALIZADO, scheduler_id="plan-inexistente")
        sigue_igual = manager.obtener(execution.id)
        self.assertEqual(sigue_igual.estado, CoordinatorState.RUNNING)


class TestCancelacion(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cancelar_ejecucion_en_curso(self):
        manager, deps = construir_manager(self.tmp)
        execution = manager.iniciar_ejecucion("Objetivo cancelable", "Criterio")
        cancelada = manager.cancelar_ejecucion(execution.id, motivo="el usuario cambió de idea")
        self.assertEqual(cancelada.estado, CoordinatorState.CANCELLED)
        self.assertIn(cancelada.objective_id, deps["objectives"].cancelados)
        self.assertIn(cancelada.workspace_id, deps["workspace"].cancelados)

    def test_no_se_puede_cancelar_una_ejecucion_terminal(self):
        manager, deps = construir_manager(self.tmp)
        execution = manager.iniciar_ejecucion("Objetivo", "Criterio")
        deps["event_bus"].emit(events.SCHEDULER_FINALIZADO, scheduler_id=execution.plan_id, evidence={})
        self.assertEqual(manager.obtener(execution.id).estado, CoordinatorState.COMPLETED)
        with self.assertRaises(CoordinatorImmutableError):
            manager.cancelar_ejecucion(execution.id, motivo="demasiado tarde")


class TestRecuperacion(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recuperar_ejecucion_terminal_no_añade_historial(self):
        store = FileCoordinatorStore(self.tmp)
        manager, deps = construir_manager(self.tmp, store=store)
        execution = manager.iniciar_ejecucion("Objetivo persistente", "Criterio")
        deps["event_bus"].emit(events.SCHEDULER_FINALIZADO, scheduler_id=execution.plan_id, evidence={})
        completada = manager.obtener(execution.id)
        n_antes = len(completada.historial)

        # Reabre con un manager nuevo apuntando al mismo store/root -- "tras
        # reiniciar EON deberá ser capaz de continuar exactamente donde
        # estaba" (ORDEN MAESTRA, "RECUPERACIÓN").
        manager2, _ = construir_manager(self.tmp, store=store)
        recuperada = manager2.recuperar(execution.id)
        self.assertEqual(recuperada.estado, CoordinatorState.COMPLETED)
        self.assertEqual(len(recuperada.historial), n_antes)

    def test_recuperar_ejecucion_no_terminal_añade_una_entrada(self):
        store = FileCoordinatorStore(self.tmp)
        manager, _ = construir_manager(self.tmp, store=store)
        execution = manager.iniciar_ejecucion("Objetivo en curso", "Criterio")
        n_antes = len(execution.historial)

        manager2, _ = construir_manager(self.tmp, store=store)
        recuperada = manager2.recuperar(execution.id)
        self.assertEqual(len(recuperada.historial), n_antes + 1)
        self.assertEqual(recuperada.historial[-1]["evento"], events.EJECUCION_RECUPERADA)

        # Idempotente por invocación: una segunda recuperación añade
        # exactamente una entrada más, nunca duplica descontroladamente.
        otra_vez = manager2.recuperar(execution.id)
        self.assertEqual(len(otra_vez.historial), n_antes + 2)

    def test_recuperar_ejecucion_inexistente_falla(self):
        store = FileCoordinatorStore(self.tmp)
        manager, _ = construir_manager(self.tmp, store=store)
        with self.assertRaises(CoordinatorRecoveryError):
            manager.recuperar("no-existe")


class TestValidators(unittest.TestCase):
    def test_transicion_ilegal_es_rechazada(self):
        with self.assertRaises(IllegalCoordinatorTransitionError):
            validar_transicion(CoordinatorState.CREATED, CoordinatorState.COMPLETED)

    def test_ningun_estado_terminal_tiene_salida(self):
        for terminal in (CoordinatorState.COMPLETED, CoordinatorState.FAILED, CoordinatorState.CANCELLED):
            for destino in CoordinatorState:
                if destino == terminal:
                    continue
                with self.assertRaises(IllegalCoordinatorTransitionError):
                    validar_transicion(terminal, destino)


class TestObtenerInexistente(unittest.TestCase):
    def test_obtener_ejecucion_inexistente(self):
        manager, _ = construir_manager(tempfile.mkdtemp())
        with self.assertRaises(CoordinatorNotFoundError):
            manager.obtener("no-existe")


class TestAislamiento(unittest.TestCase):
    """ORDEN MAESTRA, "AISLAMIENTO": el Coordinator no puede modificar
    Objectives/Planner/Scheduler/Workers/Verifier/Workspace/Package, y
    "únicamente podrá invocar sus interfaces públicas" -- lo que aquí se
    traduce en que ningún módulo de `eon.coordinator` importa código
    fuente de esos paquetes; solo puede depender de los Protocols
    declarados en `ports.py`."""

    _PROHIBIDOS = (
        "eon.objectives",
        "eon.planner",
        "eon.scheduler",
        "eon.workers",
        "eon.verifier",
        "eon.workspace",
        "eon.package",
        "eon.task_generation",
    )

    def test_ningun_modulo_importa_paquetes_del_kernel_ajenos(self):
        raiz = Path(__file__).resolve().parents[1]
        for archivo in raiz.glob("*.py"):
            arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
            for nodo in ast.walk(arbol):
                nombres = []
                if isinstance(nodo, ast.Import):
                    nombres = [a.name for a in nodo.names]
                elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                    nombres = [nodo.module]
                for nombre in nombres:
                    for prohibido in self._PROHIBIDOS:
                        self.assertFalse(
                            nombre == prohibido or nombre.startswith(prohibido + "."),
                            f"{archivo.name} importa {nombre!r}: rompe el aislamiento del Coordinator.",
                        )


class TestCierreDeEjecucionesHuerfanas(unittest.TestCase):
    """CONSOLE.md §9.1 (decisión (b)):
    `CoordinatorManager.cerrar_ejecuciones_huerfanas`."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cierra_solo_las_no_terminales_y_deja_las_terminales_intactas(self):
        manager, deps = construir_manager(self.tmp)

        huerfana = manager.iniciar_ejecucion("Objetivo huérfano", "Criterio")
        self.assertEqual(huerfana.estado, CoordinatorState.RUNNING)  # nunca se completó

        completada = manager.iniciar_ejecucion("Objetivo completo", "Criterio")
        deps["event_bus"].emit(events.SCHEDULER_FINALIZADO, scheduler_id=completada.plan_id, evidence={"ok": True})
        self.assertEqual(manager.obtener(completada.id).estado, CoordinatorState.COMPLETED)

        cerradas = manager.cerrar_ejecuciones_huerfanas()

        self.assertEqual([e.id for e in cerradas], [huerfana.id])
        self.assertEqual(manager.obtener(huerfana.id).estado, CoordinatorState.FAILED)
        # La terminal no se toca -- ni de estado ni de historial.
        self.assertEqual(manager.obtener(completada.id).estado, CoordinatorState.COMPLETED)

    def test_motivo_por_defecto_y_motivo_explicito_quedan_en_el_historial(self):
        manager, _ = construir_manager(self.tmp)
        huerfana = manager.iniciar_ejecucion("Objetivo", "Criterio")

        manager.cerrar_ejecuciones_huerfanas()

        final = manager.obtener(huerfana.id)
        ultima_entrada = final.historial[-1]
        self.assertEqual(ultima_entrada["motivo"], "proceso interrumpido antes de completar")

    def test_motivo_explicito_sobreescribe_el_por_defecto(self):
        manager, _ = construir_manager(self.tmp)
        huerfana = manager.iniciar_ejecucion("Objetivo", "Criterio")

        manager.cerrar_ejecuciones_huerfanas(motivo="mantenimiento programado")

        final = manager.obtener(huerfana.id)
        self.assertEqual(final.historial[-1]["motivo"], "mantenimiento programado")

    def test_idempotente_la_segunda_llamada_no_encuentra_nada_que_cerrar(self):
        manager, _ = construir_manager(self.tmp)
        manager.iniciar_ejecucion("Objetivo", "Criterio")

        primera = manager.cerrar_ejecuciones_huerfanas()
        segunda = manager.cerrar_ejecuciones_huerfanas()

        self.assertEqual(len(primera), 1)
        self.assertEqual(segunda, [])

    def test_sin_ejecuciones_huerfanas_devuelve_lista_vacia(self):
        manager, _ = construir_manager(self.tmp)
        self.assertEqual(manager.cerrar_ejecuciones_huerfanas(), [])

    def test_reinicio_real_del_backend_via_filecoordinatorstore_compartido(self):
        """El escenario real de la Console (§3/§9.1): un primer proceso
        deja una ejecución en RUNNING (simulando que murió a mitad); un
        SEGUNDO `CoordinatorManager`, con Objectives/Planner/Scheduler/
        Workspace/Package en memoria frescos (igual que un backend recién
        arrancado) pero el mismo `FileCoordinatorStore` sobre el mismo
        `root` en disco, debe poder ver y cerrar esa ejecución huérfana
        sin haber estado nunca en el primer proceso."""
        proceso_1, _ = construir_manager(self.tmp, store=FileCoordinatorStore(self.tmp))
        huerfana = proceso_1.iniciar_ejecucion("Objetivo del proceso 1", "Criterio")
        self.assertEqual(huerfana.estado, CoordinatorState.RUNNING)
        del proceso_1  # simula que el proceso murió: nada en memoria sobrevive

        proceso_2, _ = construir_manager(self.tmp, store=FileCoordinatorStore(self.tmp))
        cerradas = proceso_2.cerrar_ejecuciones_huerfanas()

        self.assertEqual([e.id for e in cerradas], [huerfana.id])
        self.assertEqual(proceso_2.obtener(huerfana.id).estado, CoordinatorState.FAILED)

    def test_no_reintroduce_ni_transiciones_ni_eventos_nuevos(self):
        """CONSOLE.md §9.1: 'no introduce ningún estado nuevo ni ninguna
        transición nueva'. Verificado, no solo declarado: el evento
        emitido debe ser el ya existente de fallo."""
        manager, deps = construir_manager(self.tmp)
        manager.iniciar_ejecucion("Objetivo", "Criterio")
        cantidad_antes = len(deps["event_bus"].emitidos)

        manager.cerrar_ejecuciones_huerfanas()

        nuevos = deps["event_bus"].emitidos[cantidad_antes:]
        self.assertTrue(nuevos)
        self.assertTrue(all(nombre == events.EJECUCION_FALLIDA for nombre, _kwargs in nuevos))


if __name__ == "__main__":
    unittest.main()
