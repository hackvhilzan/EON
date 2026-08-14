# Dockerfile — eon.console (CONSOLE.md, CONSOLE_DEPLOYMENT.md)
#
# Un solo proceso, un solo contenedor (CONSOLE_DEPLOYMENT.md §3.1): el
# backend HTTP y el subproceso runner (eon/console/subprocess_runner.py)
# comparten el mismo intérprete Python dentro de la misma imagen — no se
# separan en contenedores distintos, porque el backend invoca al segundo
# vía `subprocess.Popen(["python3", "-m", "eon.console.subprocess_runner", ...])`
# (CONSOLE.md §3/§6) y ambos deben ver exactamente el mismo `eon/` instalado.

FROM python:3.12-slim

# Sin build tools de más: httpx/pypdf/los SDKs de LLM son wheels puros o
# traen binarios propios; no hace falta gcc en esta imagen.
WORKDIR /app

# Capa de dependencias separada del código para aprovechar la cache de
# Docker: cambiar eon/*.py no debería forzar un reinstall de pip.
COPY eon/requirements.txt ./eon-requirements.txt
COPY eon/llm/requirements.txt ./eon-llm-requirements.txt
RUN pip install --no-cache-dir \
        $(grep -v '^#' eon-requirements.txt | grep -v '^pytest') \
        -r eon-llm-requirements.txt \
    && rm eon-requirements.txt eon-llm-requirements.txt
# Nota: se excluyen pytest/pytest-asyncio (son dependencias de desarrollo,
# no de runtime — CONSOLE_DEPLOYMENT.md no las necesita para servir tráfico).
# Se instalan los 3 SDKs de eon/llm/requirements.txt por defecto para que
# `usar_llm` (CONSOLE.md §2/§10) funcione sin importar qué
# OPENAI_API_KEY/ANTHROPIC_API_KEY/GEMINI_API_KEY termine configurando quien
# despliegue el contenedor — los imports de cada *_provider.py son
# perezosos, así que no cuesta nada en runtime no usar los otros dos.

COPY eon/ ./eon/

# CONSOLE.md §5/§9.1: todo lo que persiste (historial de ejecuciones,
# Workspace, Package) vive bajo --root, servido desde filesystem directo.
# Sin un volumen montado en /data, el contenedor es efectivamente
# stateless y pierde el historial en cada reinicio (CONSOLE_DEPLOYMENT.md §2).
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8765

# GET /estado ya existe en CONSOLE.md §5 y no toca dominio (solo lee
# entorno) — candidato natural de healthcheck, sin depender de curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8765/estado', timeout=3)" || exit 1

# --host 0.0.0.0: el 127.0.0.1 por defecto de eon/console/__main__.py solo
# tiene sentido fuera de un contenedor, donde no hay nadie afuera pidiendo
# entrar por la interfaz de loopback.
ENTRYPOINT ["python3", "-m", "eon.console", "--root", "/data", "--host", "0.0.0.0", "--port", "8765"]
