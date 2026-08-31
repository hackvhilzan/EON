#!/usr/bin/env python3
"""
Smoke test end-to-end REAL del pipeline eon.codeforces.

FUERA de pytest y FUERA de CI a propósito: llama a un modelo real (por
defecto Ollama en local) y puede tocar red -- no está en
.github/workflows/ci.yml. No inventes ni parchees nada: si esto pasa,
el pipeline real gira.

Corre eon.codeforces.solver.solve() con componentes reales -- ningún
stub:
- LLMTool sobre un proveedor real de eon.llm.provider (Ollama por
  defecto; host/API keys se leen del entorno como siempre, ver
  eon/llm/<provider>_provider.py).
- SandboxExecutor real, ejecutando cada sample con el parche de stdin.
- CFChecker real emitiendo el verdict.
- JsonlTraceSink real escribiendo a un archivo.

Problema: un fixture sintético embebido ("lee dos enteros, imprime su
suma") -- sin scraping, así este smoke no depende de Codeforces ni de
parsear HTML. Usa solve(problem, ...) (no solve_problem(url, ...)) por
eso mismo.

Códigos de salida:
    0  El loop corrió entero, escribió >= 1 trace, y al menos un
       intento tiene respuesta real del modelo (output_raw no vacío).
       PASS o FAIL del solver dan igual -- eso lo decide el modelo,
       no este script.
    1  Crash: excepción sin capturar, o el JSONL quedó vacío.
    2  Sin motor: el loop corrió y escribió traces, pero ningún
       intento llegó a obtener respuesta del modelo (todos con
       output_raw vacío -- ej. "connection refused"). No es un smoke
       en verde: no hay LLM detrás para probar nada.

Uso:
    python3 scripts/smoke_cf.py
    python3 scripts/smoke_cf.py --provider ollama --model qwen2.5-coder:7b
    python3 scripts/smoke_cf.py --provider claude --model claude-sonnet-5
    python3 scripts/smoke_cf.py --out /tmp/mis_traces.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from eon.codeforces.solver import CFProblem, CFSample, solve  # noqa: E402
from eon.codeforces.trace_logger import JsonlTraceSink, ModelInfo  # noqa: E402
from eon.llm.provider import get_provider  # noqa: E402
from eon.sandbox import SandboxExecutor  # noqa: E402
from eon.tools.llm_tool import LLMTool  # noqa: E402

_FIXTURE_PROBLEM = CFProblem(
    url="smoke://sum-two-integers",
    statement=(
        "Lee dos enteros A y B, separados por un espacio, en una sola línea de stdin. Imprime su suma en stdout."
    ),
    samples=[
        CFSample(input="1 2\n", output="3\n"),
        CFSample(input="10 20\n", output="30\n"),
        CFSample(input="-5 5\n", output="0\n"),
    ],
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test end-to-end real de eon.codeforces (fuera de pytest/CI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--provider",
        default="ollama",
        help="Proveedor LLM: ollama, claude, openai, gemini, groq, openrouter. Default: ollama.",
    )
    parser.add_argument("--model", default=None, help="Modelo a usar. Si se omite, el default del proveedor.")
    parser.add_argument("--temperature", type=float, default=0.2, help="Temperature del LLM. Default: 0.2.")
    parser.add_argument("--out", default=None, help="Ruta del JSONL de traces. Default: archivo temporal.")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Reintentos máximos por resolución. Default: 3 (igual que el solver).",
    )
    parser.add_argument(
        "--time-budget-seconds",
        type=float,
        default=45.0,
        help="Presupuesto por intento, en segundos. Default: 45.0 (igual que el solver).",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    llm_kwargs: dict[str, object] = {"temperature": args.temperature}
    if args.model:
        llm_kwargs["model"] = args.model

    try:
        llm = get_provider(args.provider, **llm_kwargs)
    except Exception as exc:
        print(f"[smoke_cf] FALLO: no se pudo construir el proveedor {args.provider!r}: {exc!r}", file=sys.stderr)
        return 1

    llm_tool = LLMTool(llm=llm)
    model_info = ModelInfo(
        name=getattr(llm, "model", args.model or ""),
        provider=llm.name,
        temperature=args.temperature,
    )

    out_path = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "eon_cf_smoke_traces.jsonl"
    sink = JsonlTraceSink(out_path)

    print(f"[smoke_cf] proveedor={llm.name} modelo={model_info.name or '(default)'} temperature={args.temperature}")
    print(f"[smoke_cf] traces -> {out_path}")
    print("[smoke_cf] resolviendo (llama a un modelo real, puede tardar)...")

    try:
        result = await solve(
            _FIXTURE_PROBLEM,
            llm_tool=llm_tool,
            executor=SandboxExecutor(),
            trace_sink=sink,
            model_info=model_info,
            max_retries=args.max_retries,
            time_budget_seconds=args.time_budget_seconds,
            checker_mode="whitespace_normalized",
            language_hint="python",
        )
    except Exception as exc:
        print(f"[smoke_cf] FALLO: el loop lanzó una excepción: {exc!r}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1

    lineas = out_path.read_text(encoding="utf-8").splitlines() if out_path.exists() else []
    if not lineas:
        print(
            "[smoke_cf] FALLO: el loop corrió sin excepción pero el JSONL quedó vacío (cero traces).", file=sys.stderr
        )
        return 1

    registros = [json.loads(linea) for linea in lineas]
    hubo_respuesta_real = any(r.get("output_raw") for r in registros)

    print()
    print("=" * 60)
    for attempt in result.attempts:
        print(f"  intento {attempt.attempt} [{attempt.language or '-'}]: {attempt.verdict} -- {attempt.detail[:200]}")
    print(f"[smoke_cf] verdict final: {'PASS' if result.solved else 'FAIL'}")
    print(f"[smoke_cf] traces escritos: {len(lineas)} -> {out_path}")
    print("=" * 60)
    print("[smoke_cf] primer trace:")
    print(json.dumps(registros[0], indent=2, ensure_ascii=False, sort_keys=True))
    print()

    if not hubo_respuesta_real:
        print(
            "[smoke_cf] SIN MOTOR: el pipeline corrió y escribió traces, pero ningún intento "
            "obtuvo respuesta del modelo (output_raw vacío en todos) -- no hay LLM alcanzable, "
            "no confundir con un smoke en verde.",
            file=sys.stderr,
        )
        return 2

    print(
        f"[smoke_cf] OK: el pipeline corrió entero y escribió {len(lineas)} trace(s) con respuesta "
        f"real del modelo ({'PASS' if result.solved else 'FAIL'} -- da igual para este smoke)."
    )
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
