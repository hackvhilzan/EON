"""
Boundary arquitectónico entre el kernel y eon.codeforces.

eon.codeforces es una capa mutable montada SOBRE los ports del kernel
(eon.sandbox, eon.tools, eon.verification), nunca al revés:

- Ningún módulo del kernel (todo eon/ salvo eon/codeforces/) puede
  importar eon.codeforces.
- El código de producción de eon/codeforces/ (sin sus tests, que
  necesitan imports más amplios para construir dobles de prueba) solo
  puede importar de eon.sandbox, eon.tools y eon.verification.

Análisis estático puro vía `ast` -- no importa ni ejecuta ningún
módulo del árbol, así que esto no crea el acoplamiento que intenta
prevenir.
"""

from __future__ import annotations

import ast
from pathlib import Path

EON_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = EON_ROOT.parent
CODEFORCES_ROOT = EON_ROOT / "codeforces"
CODEFORCES_ALLOWED_KERNEL_PACKAGES = {"codeforces", "sandbox", "tools", "verification"}


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _package_of(file_path: Path) -> str:
    """Dotted package al que pertenece `file_path`, con la misma semántica
    que usa Python para resolver imports relativos en tiempo real."""
    rel = file_path.relative_to(REPO_ROOT)
    parts = rel.parent.parts if file_path.name == "__init__.py" else rel.with_suffix("").parts[:-1]
    return ".".join(parts)


def _resolve(pkg: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    parts = pkg.split(".") if pkg else []
    strip = node.level - 1
    if strip:
        parts = parts[:-strip] if strip <= len(parts) else []
    if node.module:
        parts = [*parts, *node.module.split(".")]
    return ".".join(parts)


def _eon_imports(file_path: Path) -> set[str]:
    """Primer componente tras 'eon.' de cada módulo `eon.*` importado
    (absoluto o relativo, incluyendo `from eon import X`/`from . import X`)
    en `file_path`."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    pkg = _package_of(file_path)
    dotted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dotted.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(pkg, node)
            if base == "eon":
                for alias in node.names:
                    dotted.add(f"eon.{alias.name}")
            else:
                dotted.add(base)

    modules = set()
    for name in dotted:
        parts = name.split(".")
        if parts[0] == "eon" and len(parts) > 1:
            modules.add(parts[1])
    return modules


def test_kernel_nunca_importa_codeforces():
    ofensores = []
    for path in _iter_python_files(EON_ROOT):
        if CODEFORCES_ROOT in path.parents:
            continue
        if "codeforces" in _eon_imports(path):
            ofensores.append(str(path.relative_to(REPO_ROOT)))
    assert not ofensores, (
        "El kernel no puede importar eon.codeforces (es una capa mutable "
        f"montada sobre el kernel, no al revés) -- ofensores: {ofensores}"
    )


def test_codeforces_produccion_solo_importa_sandbox_tools_verification():
    ofensores: dict[str, list[str]] = {}
    for path in _iter_python_files(CODEFORCES_ROOT):
        if "tests" in path.relative_to(CODEFORCES_ROOT).parts:
            continue  # los tests pueden importar más para construir dobles
        extras = _eon_imports(path) - CODEFORCES_ALLOWED_KERNEL_PACKAGES
        if extras:
            ofensores[str(path.relative_to(REPO_ROOT))] = sorted(extras)
    assert not ofensores, (
        "eon/codeforces/ (producción) solo puede importar de eon.sandbox, "
        f"eon.tools y eon.verification -- ofensores: {ofensores}"
    )
