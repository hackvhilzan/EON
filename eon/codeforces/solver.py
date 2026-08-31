"""
eon.codeforces.solver
========================
Entry point standalone para resolver un problema de Codeforces:

    fetch(internet_tool) -> solve(llm_tool) -> ejecuta cada sample por
    SandboxExecutor -> cf_checker -> reintentos acotados, realimentando
    al modelo el error de compilación u output.

Standalone a propósito: no importa Coordinator ni scheduler, ni pasa
por CapabilityExecutor/ToolRegistry -- es una función async que
cualquiera puede invocar con sus propias instancias de `llm_tool` /
`internet_tool` (cualquier objeto con `async execute(**kwargs) ->
ToolResult`, la misma interfaz que ya usan LLMTool/InternetTool). El
punto de enganche para que esto se vuelva una Task/capability más del
Coordinator -- sin reescribir nada de este módulo -- está documentado
en eon/codeforces/coordinator_binding.py.

`solve_problem(url, ...)` hace fetch + parseo de HTML y delega en
`solve(problem, ...)`, que es el loop en sí y no necesita
internet_tool -- útil para correrlo contra un `CFProblem` armado a
mano (ver scripts/smoke_cf.py) sin depender de Codeforces.
"""

from __future__ import annotations

import html
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..sandbox import SandboxExecutor, SandboxProfile, SandboxResult
from ..tools.base_tool import Tool
from .cf_checker import CFChecker, CheckMode
from .trace_logger import (
    ModelInfo,
    SampleResult,
    SandboxInfo,
    SolutionInfo,
    TraceRecord,
    TraceSink,
    VerifierInfo,
)

_COMPILE_TIMEOUT_SECONDS = 10.0
_SAMPLE_TIMEOUT_SECONDS = 5.0
_DEFAULT_TIME_BUDGET_SECONDS = 45.0
_DEFAULT_MAX_RETRIES = 3


class CFFetchError(Exception):
    """No se pudo obtener o parsear el enunciado de Codeforces."""


# ─── Modelo del problema ───────────────────────────────────────


@dataclass
class CFSample:
    input: str
    output: str


@dataclass
class CFProblem:
    url: str
    statement: str
    samples: list[CFSample] = field(default_factory=list)


# ─── Fetch + parseo del HTML de Codeforces ─────────────────────

_DIV_OPEN_RE = re.compile(r"<div\b[^>]*>")
_DIV_CLOSE_RE = re.compile(r"</div>")
_SAMPLE_ITEM_RE = re.compile(r'<div class="(input|output)">.*?<pre[^>]*>(.*?)</pre>', re.S)


def _extraer_div_balanceado(texto: str, fin_apertura: int) -> str:
    """Dada la posición justo tras un '<div ...>' de apertura, devuelve
    el contenido hasta su '</div>' de cierre, balanceando los <div>
    anidados en medio (el HTML de Codeforces anida varios niveles)."""
    profundidad = 1
    pos = fin_apertura
    while profundidad > 0:
        siguiente_apertura = _DIV_OPEN_RE.search(texto, pos)
        siguiente_cierre = _DIV_CLOSE_RE.search(texto, pos)
        if siguiente_cierre is None:
            return texto[fin_apertura:]
        if siguiente_apertura is not None and siguiente_apertura.start() < siguiente_cierre.start():
            profundidad += 1
            pos = siguiente_apertura.end()
        else:
            profundidad -= 1
            pos = siguiente_cierre.end()
    return texto[fin_apertura : pos - len("</div>")]


def _buscar_contenido_div(texto: str, class_name: str) -> str | None:
    m = re.search(rf'<div class="{re.escape(class_name)}"[^>]*>', texto)
    if m is None:
        return None
    return _extraer_div_balanceado(texto, m.end())


def _pre_a_texto(pre_html: str) -> str:
    """Convierte el contenido de un <pre> de Codeforces (a veces con
    una línea por <div>) a texto plano con saltos de línea reales."""
    fragmento = re.sub(r"<br\s*/?>", "\n", pre_html)
    fragmento = re.sub(r"<div[^>]*>", "\n", fragmento)
    fragmento = re.sub(r"</div>", "", fragmento)
    fragmento = re.sub(r"<[^>]+>", "", fragmento)
    texto = html.unescape(fragmento)
    lineas = texto.split("\n")
    while lineas and lineas[0].strip() == "":
        lineas.pop(0)
    while lineas and lineas[-1].strip() == "":
        lineas.pop()
    return "\n".join(lineas) + "\n"


def _html_a_texto(fragmento: str) -> str:
    fragmento = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", fragmento, flags=re.S)
    fragmento = re.sub(r"<br\s*/?>", "\n", fragmento)
    fragmento = re.sub(r"</p>|</div>|</li>", "\n", fragmento)
    fragmento = re.sub(r"<[^>]+>", "", fragmento)
    texto = html.unescape(fragmento)
    lineas = [linea.strip() for linea in texto.splitlines()]
    return "\n".join(linea for linea in lineas if linea)


def _parsear_samples(sample_tests_html: str) -> list[CFSample]:
    entradas: list[str] = []
    salidas: list[str] = []
    for kind, pre_html in _SAMPLE_ITEM_RE.findall(sample_tests_html):
        texto = _pre_a_texto(pre_html)
        (entradas if kind == "input" else salidas).append(texto)
    return [CFSample(i, o) for i, o in zip(entradas, salidas, strict=False)]


def parse_problem_html(url: str, html_text: str) -> CFProblem:
    """Parsea el HTML de una página de problema de Codeforces.

    Best-effort: cubre la estructura estándar (`problem-statement` >
    `sample-tests` > pares `input`/`output` con `<pre>`), no un HTML
    arbitrario.
    """
    statement_html = _buscar_contenido_div(html_text, "problem-statement")
    if statement_html is None:
        raise CFFetchError(f"No se encontró 'problem-statement' en {url}")

    sample_tests_html = _buscar_contenido_div(statement_html, "sample-tests") or statement_html
    samples = _parsear_samples(sample_tests_html)
    if not samples:
        raise CFFetchError(f"No se encontraron samples en {url}")

    statement = _html_a_texto(statement_html)
    return CFProblem(url=url, statement=statement, samples=samples)


async def fetch_problem(internet_tool: Tool, url: str) -> CFProblem:
    """Descarga y parsea el enunciado + samples de un problema."""
    result = await internet_tool.execute(url=url)
    if not result.ok:
        raise CFFetchError(f"No se pudo obtener {url}: {result.error}")
    data = result.data
    html_text = data["text"] if isinstance(data, dict) else str(data)
    return parse_problem_html(url, html_text)


# ─── Prompt + parseo de la solución del LLM ────────────────────

_LANG_FENCE_RE = re.compile(r"```(python|cpp|c\+\+)\s*\n(.*?)```", re.S | re.I)


def _build_prompt(problem: CFProblem, feedback: str | None, *, language_hint: str | None = None) -> str:
    if language_hint == "python":
        instruccion = (
            "Resuelve el siguiente problema de Codeforces en Python. "
            "Responde con un único bloque de código en un fence "
            "```python. El programa debe leer la entrada de stdin y "
            "escribir la respuesta en stdout, sin texto adicional "
            "fuera del bloque de código."
        )
    else:
        instruccion = (
            "Resuelve el siguiente problema de Codeforces. Responde con un "
            "único bloque de código en un fence ```python o ```cpp. El "
            "programa debe leer la entrada de stdin y escribir la respuesta "
            "en stdout, sin texto adicional fuera del bloque de código."
        )
    partes = [
        instruccion,
        f"Enunciado:\n{problem.statement}",
    ]
    if problem.samples:
        ejemplos = "\n\n".join(f"Entrada:\n{s.input}Salida esperada:\n{s.output}" for s in problem.samples)
        partes.append(f"Ejemplos:\n{ejemplos}")
    if feedback:
        partes.append(f"El intento anterior falló:\n{feedback}\nCorrige el código.")
    return "\n\n".join(partes)


def _extraer_solucion(texto_llm: str) -> tuple[str, str] | None:
    m = _LANG_FENCE_RE.search(texto_llm)
    if m is None:
        return None
    lang = m.group(1).lower()
    lang = "cpp" if lang == "c++" else lang
    return m.group(2), lang


# ─── Ejecución de samples en el sandbox, verificados por CFChecker ──

_checker = CFChecker()


def _ejecutar_samples(
    samples: list[CFSample],
    checker_mode: CheckMode,
    deadline: float,
    ejecutar_uno,
) -> tuple[str, str, list[SampleResult], SandboxResult | None]:
    sample_results: list[SampleResult] = []
    ultimo_sandbox: SandboxResult | None = None
    for i, sample in enumerate(samples, start=1):
        restante = deadline - time.monotonic()
        if restante <= 0:
            return "fail", f"Presupuesto de tiempo agotado antes del sample {i}", sample_results, ultimo_sandbox
        profile = SandboxProfile(
            profile_id="cf-run",
            max_duration_seconds=min(_SAMPLE_TIMEOUT_SECONDS, restante),
        )
        result: SandboxResult = ejecutar_uno(sample, profile)
        ultimo_sandbox = result
        if result.timed_out:
            motivo = f"timeout tras {profile.max_duration_seconds:.1f}s"
            sample_results.append(SampleResult(i - 1, False, motivo))
            return "fail", f"Sample {i}: {motivo}", sample_results, ultimo_sandbox
        if not result.success:
            motivo = f"exit_code={result.exit_code} stderr={result.stderr[:500]}"
            sample_results.append(SampleResult(i - 1, False, motivo))
            return "fail", f"Sample {i}: {motivo}", sample_results, ultimo_sandbox

        layer = _checker.verificar(
            objective=None,
            evidence={"actual": result.stdout, "expected": sample.output, "mode": checker_mode},
        )
        sample_results.append(SampleResult(i - 1, layer.passed, layer.motivo))
        if not layer.passed:
            return "fail", f"Sample {i}: {layer.motivo}", sample_results, ultimo_sandbox
    return "pass", "todos los samples pasaron", sample_results, ultimo_sandbox


def _resolver_y_verificar(
    executor: SandboxExecutor,
    code: str,
    language: str,
    samples: list[CFSample],
    checker_mode: CheckMode,
    deadline: float,
) -> tuple[str, str, list[SampleResult], SandboxResult | None]:
    """Compila (si aplica) y ejecuta `code` contra cada sample.

    Devuelve (verdict, detail, sample_results, sandbox_result) con
    verdict en {"pass", "compile_error", "fail"}.
    """
    if language == "python":
        return _ejecutar_samples(
            samples,
            checker_mode,
            deadline,
            lambda sample, profile: executor.run_python(code, profile=profile, stdin=sample.input),
        )

    if language == "cpp":
        with tempfile.TemporaryDirectory(prefix="eon_cf_build_") as build_dir:
            source_path = Path(build_dir) / "main.cpp"
            binary_path = Path(build_dir) / "main"
            source_path.write_text(code, encoding="utf-8")

            compile_profile = SandboxProfile(
                profile_id="cf-compile",
                max_duration_seconds=min(_COMPILE_TIMEOUT_SECONDS, max(1.0, deadline - time.monotonic())),
            )
            compile_result = executor.run_command(
                ["g++", "-O2", "-std=c++17", "-o", str(binary_path), str(source_path)],
                profile=compile_profile,
            )
            if not compile_result.success:
                detail = compile_result.stderr or "Error de compilación desconocido"
                return "compile_error", detail, [], compile_result

            return _ejecutar_samples(
                samples,
                checker_mode,
                deadline,
                lambda sample, profile: executor.run_command([str(binary_path)], profile=profile, stdin=sample.input),
            )

    return "fail", f"Lenguaje no soportado: {language!r} (usa python o cpp)", [], None


# ─── Loop principal ─────────────────────────────────────────────


@dataclass
class SolveAttempt:
    attempt: int
    language: str | None
    verdict: str
    detail: str


@dataclass
class SolveResult:
    solved: bool
    attempts: list[SolveAttempt]
    code: str | None = None
    language: str | None = None


def _sandbox_info(result: SandboxResult | None) -> SandboxInfo | None:
    if result is None:
        return None
    return SandboxInfo(exit_code=result.exit_code, timed_out=result.timed_out, duration_s=result.duration_seconds)


def _log_attempt(
    trace_sink: TraceSink | None,
    *,
    problem: CFProblem,
    prompt: str,
    model_info: ModelInfo,
    output_raw: str,
    solution: SolutionInfo | None,
    attempt_num: int,
    verdict: str,
    checker_mode: CheckMode,
    sample_results: list[SampleResult],
    sandbox_result: SandboxResult | None,
    detail: str | None,
) -> None:
    if trace_sink is None:
        return
    record = TraceRecord.create(
        domain="codeforces",
        problem_id=problem.url,
        objective=problem.statement,
        prompt=prompt,
        model=model_info,
        output_raw=output_raw,
        solution=solution,
        attempt_index=attempt_num - 1,
        verdict="PASS" if verdict == "pass" else "FAIL",
        verifier=VerifierInfo(name=_checker.name, mode=checker_mode),
        sample_results=sample_results,
        sandbox=_sandbox_info(sandbox_result),
        error=None if verdict == "pass" else detail,
    )
    trace_sink.write(record)


async def solve_problem(
    problem_url: str,
    *,
    internet_tool: Tool,
    llm_tool: Tool,
    executor: SandboxExecutor | None = None,
    trace_sink: TraceSink | None = None,
    model_info: ModelInfo | None = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    time_budget_seconds: float = _DEFAULT_TIME_BUDGET_SECONDS,
    checker_mode: CheckMode = "whitespace_normalized",
    language_hint: str | None = None,
) -> SolveResult:
    """Resuelve `problem_url`: fetch(internet_tool) -> solve() -- ver
    `solve()` para el loop en sí (fetch es lo único que esta función
    añade encima).
    """
    problem = await fetch_problem(internet_tool, problem_url)
    return await solve(
        problem,
        llm_tool=llm_tool,
        executor=executor,
        trace_sink=trace_sink,
        model_info=model_info,
        max_retries=max_retries,
        time_budget_seconds=time_budget_seconds,
        checker_mode=checker_mode,
        language_hint=language_hint,
    )


async def solve(
    problem: CFProblem,
    *,
    llm_tool: Tool,
    executor: SandboxExecutor | None = None,
    trace_sink: TraceSink | None = None,
    model_info: ModelInfo | None = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    time_budget_seconds: float = _DEFAULT_TIME_BUDGET_SECONDS,
    checker_mode: CheckMode = "whitespace_normalized",
    language_hint: str | None = None,
) -> SolveResult:
    """El loop real: solve(LLM) -> sandbox -> cf_checker, con hasta
    `max_retries` intentos realimentando el error al modelo, dado un
    `CFProblem` ya construido (sin fetch/parseo de HTML) -- útil para
    problemas armados a mano (fixtures sintéticos, scripts/smoke_cf.py)
    sin depender de internet_tool ni de Codeforces. `solve_problem()`
    es un wrapper de esta función que primero hace el fetch.

    Si se inyecta `trace_sink` (protocolo TraceSink, ver trace_logger.py),
    cada intento -- PASS o FAIL -- se registra tras su verify vía
    `trace_sink.write()`. Sin `trace_sink` no cambia nada del
    comportamiento existente.

    `language_hint="python"` fuerza el prompt a pedir solo Python (sin
    ofrecer C++ como opción) -- útil para no necesitar g++ en el
    sandbox.
    """
    executor = executor or SandboxExecutor()
    model_info = model_info or ModelInfo()

    attempts: list[SolveAttempt] = []
    feedback: str | None = None

    for attempt_num in range(1, max_retries + 1):
        deadline = time.monotonic() + time_budget_seconds
        prompt = _build_prompt(problem, feedback, language_hint=language_hint)

        llm_result = await llm_tool.execute(prompt=prompt)
        if not llm_result.ok:
            detail = f"Error del LLM: {llm_result.error}"
            attempts.append(SolveAttempt(attempt_num, None, "fail", detail))
            _log_attempt(
                trace_sink,
                problem=problem,
                prompt=prompt,
                model_info=model_info,
                output_raw="",
                solution=None,
                attempt_num=attempt_num,
                verdict="fail",
                checker_mode=checker_mode,
                sample_results=[],
                sandbox_result=None,
                detail=detail,
            )
            feedback = detail
            continue

        output_raw = str(llm_result.data or "")
        parsed = _extraer_solucion(output_raw)
        if parsed is None:
            detail = "La respuesta del LLM no contiene un bloque ```python o ```cpp."
            attempts.append(SolveAttempt(attempt_num, None, "fail", detail))
            _log_attempt(
                trace_sink,
                problem=problem,
                prompt=prompt,
                model_info=model_info,
                output_raw=output_raw,
                solution=None,
                attempt_num=attempt_num,
                verdict="fail",
                checker_mode=checker_mode,
                sample_results=[],
                sandbox_result=None,
                detail=detail,
            )
            feedback = detail
            continue

        code, language = parsed
        verdict, detail, sample_results, sandbox_result = _resolver_y_verificar(
            executor, code, language, problem.samples, checker_mode, deadline
        )
        attempts.append(SolveAttempt(attempt_num, language, verdict, detail))
        _log_attempt(
            trace_sink,
            problem=problem,
            prompt=prompt,
            model_info=model_info,
            output_raw=output_raw,
            solution=SolutionInfo(code=code, lang=language),
            attempt_num=attempt_num,
            verdict=verdict,
            checker_mode=checker_mode,
            sample_results=sample_results,
            sandbox_result=sandbox_result,
            detail=detail,
        )
        if verdict == "pass":
            return SolveResult(True, attempts, code, language)
        feedback = detail

    return SolveResult(False, attempts)
