"""Tests de `eon.capabilities` -- el punto de integración entre el Kernel
(`eon.workers.executor.EjecutorDeCapability`) y `eon.tools`/`eon.llm`.

Usa deliberadamente solo Tools sin dependencias externas (`filesystem`,
`python`, ambas solo librería estándar) para no requerir `httpx` instalado --
`api`/`internet`/`calendar` (Google) se ejercitan por separado si el entorno
las trae; `ToolRegistry.autodiscover()` ya tolera su ausencia sin fallar
(ver `eon/tools/registry.py`).
"""

from __future__ import annotations

import pytest

from eon.capabilities import CapabilityExecutor
from eon.capabilities.capability_map import CapabilityNotMappedError, CapabilitySpec, resolver


def test_resolver_capability_conocida():
    spec = resolver("tool.filesystem")
    assert spec.tool_name == "filesystem"


def test_resolver_capability_desconocida():
    with pytest.raises(CapabilityNotMappedError):
        resolver("tool.no_existe")


def test_terminal_y_python_no_estan_mapeados_por_defecto():
    """Decisión explícita de CAPABILITIES.md §3: las dos Tools de mayor
    riesgo (shell/exec arbitrario) no entran al mapa por defecto."""
    for capability_id in ("tool.terminal", "tool.python"):
        with pytest.raises(CapabilityNotMappedError):
            resolver(capability_id)


def test_resolver_acepta_un_mapa_a_medida():
    mapa = {"mi.capability": CapabilitySpec(tool_name="python")}
    spec = resolver("mi.capability", mapa)
    assert spec.tool_name == "python"
    with pytest.raises(CapabilityNotMappedError):
        resolver("tool.filesystem", mapa)  # el mapa a medida no hereda el default


@pytest.fixture
def executor():
    ex = CapabilityExecutor()
    yield ex
    ex.cerrar()


def test_capability_filesystem_write_and_read(tmp_path, executor):
    destino = tmp_path / "salida.txt"

    ok = executor("tool.filesystem", {"action": "write", "path": str(destino), "content": "hola"})
    assert ok is True
    assert executor.ultimo_resultado.ok is True
    assert destino.read_text(encoding="utf-8") == "hola"

    ok = executor("tool.filesystem", {"action": "read", "path": str(destino)})
    assert ok is True
    assert executor.ultimo_resultado.data == "hola"


def test_capability_id_no_mapeado_devuelve_false_sin_excepcion(executor):
    ok = executor("capability.inexistente", {})
    assert ok is False
    assert executor.ultimo_resultado.ok is False


def test_capability_con_error_de_tool_devuelve_false_sin_excepcion(executor):
    ok = executor("tool.filesystem", {"action": "read", "path": "/ruta/que/no/existe/xyz"})
    assert ok is False
    assert executor.ultimo_resultado.ok is False


def test_executor_es_reutilizable_entre_llamadas(tmp_path, executor):
    """El loop dedicado se crea una vez, no por llamada -- varias
    invocaciones seguidas deben funcionar sobre el mismo hilo/loop."""
    for i in range(5):
        destino = tmp_path / f"n{i}.txt"
        ok = executor("tool.filesystem", {"action": "write", "path": str(destino), "content": str(i)})
        assert ok is True
        assert destino.read_text(encoding="utf-8") == str(i)


def test_cerrar_es_idempotente_y_bloquea_nuevas_llamadas(tmp_path):
    ex = CapabilityExecutor()
    ex.cerrar()
    ex.cerrar()  # no debe lanzar por llamarse dos veces

    ok = ex("tool.filesystem", {"action": "write", "path": str(tmp_path / "x.txt"), "content": "x"})
    assert ok is False
    assert "cerrado" in ex.ultimo_resultado.error


def test_context_manager_cierra_automaticamente(tmp_path):
    with CapabilityExecutor() as ex:
        ok = ex("tool.filesystem", {"action": "write", "path": str(tmp_path / "x.txt"), "content": "x"})
        assert ok is True
    assert ex._cerrado is True
