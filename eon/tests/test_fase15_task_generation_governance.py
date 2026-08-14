"""Fase 15 (Autonomous Task Generation / Planning Intelligence) -- E2E.

    Objective -> LLM Task Generator -> TaskSpec[] -> VALIDATOR -> Planner
              -> Scheduler -> Workers -> Tools/LLM -> Verifier -> Workspace
              -> Package READY

Regla verificada de punta a punta: "El LLM propone. EON valida, gobierna y
ejecuta." Usa un Fake LLM (satisface `eon.llm.base.LLM` por duck typing) --
nunca un proveedor real, sin red ni credenciales, mismo criterio que
`tests/test_e2e_llm_capability.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eon.coordinator.exceptions import DelegationError
from eon.llm.base import LLM
from eon.runtime import KernelRuntime
from eon.task_generation import (
    LLMTaskGenerator,
    TaskSpecValidator,
    ValidatingTaskGenerator,
)

_CAPABILITIES_VALIDAS = ["tool.filesystem", "tool.internet"]


class _FakeLLM(LLM):
    name = "fake"

    def __init__(self, respuesta: str) -> None:
        self._respuesta = respuesta

    def generate(self, prompt: str) -> str:
        return self._respuesta


def _ejecutor_siempre_exitoso(capability_id: str, parametros: dict) -> bool:
    return True


def _task_generation(respuesta_llm: str) -> ValidatingTaskGenerator:
    """ "El LLM propone" (`LLMTaskGenerator`) + "EON valida, gobierna"
    (`TaskSpecValidator`), compuestos como se recomienda inyectar en
    producción -- ver `eon/task_generation/validating.py`."""
    return ValidatingTaskGenerator(
        LLMTaskGenerator(_FakeLLM(respuesta_llm), capabilities_validas=_CAPABILITIES_VALIDAS),
        TaskSpecValidator(capabilities_validas=_CAPABILITIES_VALIDAS),
    )


def test_llm_propone_una_task_valida_y_el_pipeline_completa(tmp_path: Path) -> None:
    respuesta = json.dumps([{"capability_id": "tool.filesystem", "parametros": {"path": "/tmp/x"}}])
    runtime = KernelRuntime(
        root=tmp_path,
        task_executor=_ejecutor_siempre_exitoso,
        task_generation=_task_generation(respuesta),
        worker_capabilities=_CAPABILITIES_VALIDAS,
    )
    result = runtime.run("Objetivo generado por LLM", "El objetivo debe completarse.")

    assert result.package_state == "ready"
    package = runtime.package_manager.obtener(result.package_id)
    assert package.estado.value == "ready"


def test_llm_propone_varias_tasks_encadenadas_y_el_pipeline_completa(tmp_path: Path) -> None:
    respuesta = json.dumps(
        [
            {"capability_id": "tool.filesystem", "id": "paso-1"},
            {"capability_id": "tool.internet", "id": "paso-2", "depende_de": ["paso-1"]},
        ]
    )
    runtime = KernelRuntime(
        root=tmp_path,
        task_executor=_ejecutor_siempre_exitoso,
        task_generation=_task_generation(respuesta),
        worker_capabilities=_CAPABILITIES_VALIDAS,
    )
    result = runtime.run("Objetivo multi-task", "El objetivo debe completarse.")

    assert result.package_state == "ready"


def test_capability_fuera_del_conjunto_permitido_es_rechazada_antes_de_planificar(
    tmp_path: Path,
) -> None:
    """Núcleo de la regla de Fase 15: un LLM que propone una capability no
    autorizada (p.ej. 'tool.terminal', deliberadamente fuera del mapa por
    riesgo -- ver `eon/capabilities/capability_map.py`) nunca llega a
    convertirse en Task real ni a ejecutarse. El Coordinator traduce el
    rechazo del validador en `DelegationError` (`_solicitar_plan` ->
    `TaskGenerationPort.generar`), la misma vía por la que cualquier fallo
    de un módulo delegado se propaga -- ver `coordinator/manager.py`."""
    respuesta = json.dumps([{"capability_id": "tool.terminal"}])
    runtime = KernelRuntime(
        root=tmp_path,
        task_executor=_ejecutor_siempre_exitoso,
        task_generation=_task_generation(respuesta),
        worker_capabilities=_CAPABILITIES_VALIDAS,
    )

    with pytest.raises(DelegationError):
        runtime.run("Objetivo con capability no autorizada", "El objetivo debe completarse.")


def test_dependencia_a_id_inexistente_propuesta_por_el_llm_es_rechazada(tmp_path: Path) -> None:
    respuesta = json.dumps([{"capability_id": "tool.filesystem", "depende_de": ["no-existe"]}])
    runtime = KernelRuntime(
        root=tmp_path,
        task_executor=_ejecutor_siempre_exitoso,
        task_generation=_task_generation(respuesta),
        worker_capabilities=_CAPABILITIES_VALIDAS,
    )

    with pytest.raises(DelegationError):
        runtime.run("Objetivo con dependencia inválida", "El objetivo debe completarse.")


def test_respuesta_del_llm_no_parseable_como_json_es_rechazada(tmp_path: Path) -> None:
    runtime = KernelRuntime(
        root=tmp_path,
        task_executor=_ejecutor_siempre_exitoso,
        task_generation=_task_generation("esto no es JSON en absoluto"),
        worker_capabilities=_CAPABILITIES_VALIDAS,
    )

    with pytest.raises(DelegationError):
        runtime.run("Objetivo con respuesta LLM inválida", "El objetivo debe completarse.")
