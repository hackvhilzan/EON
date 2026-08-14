"""
conftest.py raíz de EON.

Configuración compartida para todos los tests del kernel.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Directorio temporal aislado para tests que necesitan persistencia."""
    root = tmp_path / "eon_test"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asegura que los tests no lean variables de entorno del sistema real
    que puedan afectar el comportamiento del kernel (API keys, provider, etc.).
    Los tests que necesitan una variable deben setearla explícitamente."""
    for key in [
        "EON_LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "EON_MEMORY_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def no_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture para tests que no deben tocar ningún proveedor LLM real."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
