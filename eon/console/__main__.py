"""
eon.console.__main__
======================
Punto de entrada para `python3 -m eon.console`.

Uso:
    python3 -m eon.console --root /data --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import argparse
import logging

from .server import ConsoleServer


def main() -> None:
    parser = argparse.ArgumentParser(description="Consola HTTP de EON")
    parser.add_argument("--root", default=".eon_runtime", help="Directorio raíz de datos")
    parser.add_argument("--host", default="127.0.0.1", help="Host de escucha")
    parser.add_argument("--port", type=int, default=8765, help="Puerto de escucha")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    server = ConsoleServer(root=args.root, host=args.host, port=args.port)
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
