from __future__ import annotations

import pytest

from eon.llm.base import LLM
from eon.sandbox import SandboxExecutor
from eon.tools.base_tool import ToolResult
from eon.tools.llm_tool import LLMTool

from ..solver import (
    CFFetchError,
    _extraer_solucion,
    fetch_problem,
    parse_problem_html,
    solve_problem,
)

_PROBLEM_HTML = """
<html><body>
<div class="problem-statement">
<div class="header">
<div class="title">A. Suma de dos numeros</div>
</div>
<div>Dado dos enteros <var>a</var> y <var>b</var>, imprime su suma.</div>
<div class="sample-tests">
<div class="sample-test">
<div class="input">
<div class="title">Input</div>
<pre>
1 2
</pre>
</div>
<div class="output">
<div class="title">Output</div>
<pre>
3
</pre>
</div>
</div>
<div class="sample-test">
<div class="input">
<div class="title">Input</div>
<pre>
10 20
</pre>
</div>
<div class="output">
<div class="title">Output</div>
<pre>
30
</pre>
</div>
</div>
</div>
</div>
</body></html>
"""

_PY_SOLUCION_CORRECTA = "```python\nimport sys\na, b = map(int, sys.stdin.read().split())\nprint(a + b)\n```"
_PY_SOLUCION_INCORRECTA = "```python\nimport sys\na, b = map(int, sys.stdin.read().split())\nprint(a - b)\n```"
_CPP_SOLUCION_CORRECTA = (
    "```cpp\n#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<(a+b)<<std::endl;}\n```"
)


class _FakeInternetTool:
    def __init__(self, html_text: str | None = None, error: str | None = None):
        self._html = html_text
        self._error = error

    async def execute(self, url: str, **kwargs) -> ToolResult:
        if self._error is not None:
            return ToolResult(ok=False, error=self._error)
        return ToolResult(ok=True, data={"text": self._html})


class _LLMDeRespuestasEnSecuencia(LLM):
    name = "secuencia"

    def __init__(self, respuestas: list[str]):
        self._respuestas = list(respuestas)
        self.prompts_recibidos: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts_recibidos.append(prompt)
        return self._respuestas.pop(0)


# ─── Parseo de la respuesta del LLM ─────────────────────────


class TestExtraerSolucion:
    def test_extrae_bloque_python(self):
        code, lang = _extraer_solucion(_PY_SOLUCION_CORRECTA)
        assert lang == "python"
        assert "print(a + b)" in code

    def test_extrae_bloque_cpp(self):
        code, lang = _extraer_solucion(_CPP_SOLUCION_CORRECTA)
        assert lang == "cpp"
        assert "#include" in code

    def test_sin_bloque_de_codigo_devuelve_none(self):
        assert _extraer_solucion("no hay código aquí") is None


# ─── Parseo del HTML de Codeforces ───────────────────────────


class TestParseProblemHtml:
    def test_extrae_samples_y_statement(self):
        problem = parse_problem_html("https://codeforces.com/problemset/problem/1/A", _PROBLEM_HTML)
        assert len(problem.samples) == 2
        assert problem.samples[0].input == "1 2\n"
        assert problem.samples[0].output == "3\n"
        assert problem.samples[1].input == "10 20\n"
        assert problem.samples[1].output == "30\n"
        assert "Suma de dos numeros" in problem.statement

    def test_sin_problem_statement_lanza_error(self):
        with pytest.raises(CFFetchError):
            parse_problem_html("https://x", "<html><body>nada aquí</body></html>")


class TestFetchProblem:
    async def test_fetch_delega_en_el_internet_tool(self):
        problem = await fetch_problem(_FakeInternetTool(_PROBLEM_HTML), "https://cf/1A")
        assert problem.url == "https://cf/1A"
        assert len(problem.samples) == 2

    async def test_fetch_propaga_error_del_tool(self):
        with pytest.raises(CFFetchError):
            await fetch_problem(_FakeInternetTool(error="timeout"), "https://cf/1A")


# ─── Loop solve_problem ──────────────────────────────────────


class TestSolveProblem:
    async def test_resuelve_en_python_al_primer_intento(self):
        llm = _LLMDeRespuestasEnSecuencia([_PY_SOLUCION_CORRECTA])
        result = await solve_problem(
            "https://cf/1A",
            internet_tool=_FakeInternetTool(_PROBLEM_HTML),
            llm_tool=LLMTool(llm=llm),
            executor=SandboxExecutor(),
        )
        assert result.solved
        assert len(result.attempts) == 1
        assert result.attempts[0].verdict == "pass"
        assert result.language == "python"

    async def test_resuelve_en_cpp_compilando_y_ejecutando(self):
        llm = _LLMDeRespuestasEnSecuencia([_CPP_SOLUCION_CORRECTA])
        result = await solve_problem(
            "https://cf/1A",
            internet_tool=_FakeInternetTool(_PROBLEM_HTML),
            llm_tool=LLMTool(llm=llm),
            executor=SandboxExecutor(),
        )
        assert result.solved
        assert result.language == "cpp"

    async def test_reintenta_y_realimenta_el_error_al_modelo(self):
        llm = _LLMDeRespuestasEnSecuencia([_PY_SOLUCION_INCORRECTA, _PY_SOLUCION_CORRECTA])
        result = await solve_problem(
            "https://cf/1A",
            internet_tool=_FakeInternetTool(_PROBLEM_HTML),
            llm_tool=LLMTool(llm=llm),
            executor=SandboxExecutor(),
        )
        assert result.solved
        assert len(result.attempts) == 2
        assert result.attempts[0].verdict == "fail"
        assert "Sample 1" in result.attempts[0].detail
        # el feedback del primer fallo se realimentó en el segundo prompt
        assert "Sample 1" in llm.prompts_recibidos[1]

    async def test_agota_reintentos_si_el_llm_nunca_produce_codigo_valido(self):
        llm = _LLMDeRespuestasEnSecuencia(["no hay código aquí"] * 3)
        result = await solve_problem(
            "https://cf/1A",
            internet_tool=_FakeInternetTool(_PROBLEM_HTML),
            llm_tool=LLMTool(llm=llm),
            executor=SandboxExecutor(),
            max_retries=3,
        )
        assert not result.solved
        assert len(result.attempts) == 3
        assert all(a.verdict == "fail" for a in result.attempts)
