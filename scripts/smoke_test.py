#!/usr/bin/env python3
"""
Smoke test end-to-end de EON.

Verifica que el kernel funciona de punta a punta:
1. Levanta la consola HTTP en un puerto aleatorio.
2. Verifica health check (GET /estado).
3. Crea una ejecución (POST /ejuciones).
4. Verifica que la ejecución terminó en estado COMPLETED.
5. Lista ejecuciones (GET /ejecuciones).
6. Cierra la consola.

Uso:
    python3 scripts/smoke_test.py

Sale con código 0 si todo pasa, 1 si algo falla.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

# Asegurar que eon/ está en el path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 10.0) -> bool:
    """Espera a que el servidor responda en /estado."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/estado", timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def main() -> int:
    port = _find_free_port()
    root = REPO_ROOT / ".eon_smoke_test"

    print(f"[smoke] Levantando consola en puerto {port}...")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "eon.console",
            "--root", str(root),
            "--host", "127.0.0.1",
            "--port", str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )

    try:
        # 1. Health check
        print("[smoke] Esperando servidor...")
        if not _wait_for_server(port):
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            print(f"[FAIL] El servidor no respondió en {port}.")
            print(f"  stderr: {stderr[:500]}")
            return 1
        print("[OK] Servidor levantado.")

        # 2. GET /estado
        resp = httpx.get(f"http://127.0.0.1:{port}/estado", timeout=5.0)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert resp.json()["status"] == "ok"
        print("[OK] GET /estado → 200 {\"status\": \"ok\"}")

        # 3. POST /ejecuciones — crear y ejecutar
        print("[smoke] Creando ejecución...")
        resp = httpx.post(
            f"http://127.0.0.1:{port}/ejecuciones",
            json={
                "descripcion": "Smoke test: verificar que el kernel funciona end-to-end",
                "criterio_de_exito": "La ejecución termina sin error",
            },
            timeout=30.0,
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        body = resp.json()
        execution_id = body["execution_id"]
        package_id = body["package_id"]
        package_state = body["package_state"]
        assert package_state == "ready", f"Expected package_state=ready, got {package_state}"
        print(f"[OK] POST /ejecuciones → 201 (exec={execution_id[:8]}..., pkg={package_id[:8]}..., state={package_state})")

        # 4. GET /ejecuciones/{id}
        resp = httpx.get(f"http://127.0.0.1:{port}/ejecuciones/{execution_id}", timeout=5.0)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        detail = resp.json()
        assert detail["id"] == execution_id
        assert detail["estado"] == "completed", f"Expected estado=completed, got {detail['estado']}"
        print(f"[OK] GET /ejecuciones/{execution_id[:8]}... → 200 (estado={detail['estado']})")

        # 5. GET /ejecuciones — listar
        resp = httpx.get(f"http://127.0.0.1:{port}/ejecuciones", timeout=5.0)
        assert resp.status_code == 200
        ejecuciones = resp.json()["ejecuciones"]
        assert len(ejecuciones) >= 1
        assert any(e["id"] == execution_id for e in ejecuciones)
        print(f"[OK] GET /ejecuciones → 200 ({len(ejecuciones)} ejecución(es))")

        # 6. GET ruta inexistente → 404
        resp = httpx.get(f"http://127.0.0.1:{port}/no-existe", timeout=5.0)
        assert resp.status_code == 404
        print("[OK] GET /no-existe → 404")

        print()
        print("=" * 50)
        print("  SMOKE TEST PASSED ✓")
        print("  Todos los endpoints funcionan correctamente.")
        print("=" * 50)
        return 0

    except Exception as exc:
        print(f"[FAIL] {exc}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        proc.terminate()
        proc.wait(timeout=5)
        # Limpiar datos temporales
        import shutil
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
