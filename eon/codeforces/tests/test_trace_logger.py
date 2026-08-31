from __future__ import annotations

from ..trace_logger import (
    JsonlTraceSink,
    ModelInfo,
    SampleResult,
    SandboxInfo,
    SolutionInfo,
    TraceRecord,
    VerifierInfo,
)


class _InMemorySink:
    def __init__(self) -> None:
        self.records: list[TraceRecord] = []

    def write(self, record: TraceRecord) -> None:
        self.records.append(record)


def _crear_record(**overrides) -> TraceRecord:
    base = {
        "domain": "codeforces",
        "problem_id": "https://codeforces.com/problemset/problem/1/A",
        "objective": "Suma dos enteros.",
        "prompt": "Resuelve...",
        "model": ModelInfo(name="claude-sonnet-5", provider="claude", temperature=0.0),
        "output_raw": "```python\nprint(1)\n```",
        "solution": SolutionInfo(code="print(1)", lang="python"),
        "attempt_index": 0,
        "verdict": "PASS",
        "verifier": VerifierInfo(name="cf_checker", mode="whitespace_normalized"),
        "sample_results": [SampleResult(index=0, passed=True, motivo="coincidencia tras normalizar espacios")],
        "sandbox": SandboxInfo(exit_code=0, timed_out=False, duration_s=0.05),
    }
    base.update(overrides)
    return TraceRecord.create(**base)


class TestTraceSinkRegistraPassYFail:
    def test_un_pass_se_registra_con_los_campos_correctos(self):
        sink = _InMemorySink()
        record = _crear_record(verdict="PASS")
        sink.write(record)

        assert len(sink.records) == 1
        registrado = sink.records[0]
        assert registrado.verdict == "PASS"
        assert registrado.domain == "codeforces"
        assert registrado.attempt_index == 0
        assert registrado.solution == SolutionInfo(code="print(1)", lang="python")
        assert registrado.sample_results[0].passed
        assert registrado.error is None

    def test_un_fail_tambien_se_registra_no_se_descarta(self):
        sink = _InMemorySink()
        record = _crear_record(
            verdict="FAIL",
            solution=SolutionInfo(code="print(2)", lang="python"),
            sample_results=[
                SampleResult(index=0, passed=False, motivo="mismatch normalizado: esperado='1' obtenido='2'")
            ],
            error="Sample 1: mismatch normalizado: esperado='1' obtenido='2'",
        )
        sink.write(record)

        assert len(sink.records) == 1
        registrado = sink.records[0]
        assert registrado.verdict == "FAIL"
        assert not registrado.sample_results[0].passed
        assert registrado.error is not None


class TestIdReproducible:
    def test_dos_outputs_identicos_dan_el_mismo_id(self):
        record_a = _crear_record()
        record_b = _crear_record()
        assert record_a.id == record_b.id

    def test_ts_distinto_no_cambia_el_id(self):
        record_a = _crear_record(ts="2026-01-01T00:00:00+00:00")
        record_b = _crear_record(ts="2026-01-02T00:00:00+00:00")
        assert record_a.id == record_b.id

    def test_contenido_distinto_da_id_distinto(self):
        record_a = _crear_record(verdict="PASS")
        record_b = _crear_record(verdict="FAIL")
        assert record_a.id != record_b.id

    def test_id_no_esta_vacio(self):
        assert _crear_record().id


class TestJsonlTraceSink:
    def test_escribe_una_linea_json_por_record_y_crea_dirs(self, tmp_path):
        destino = tmp_path / "nested" / "traces.jsonl"
        sink = JsonlTraceSink(destino)

        sink.write(_crear_record(verdict="PASS"))
        sink.write(_crear_record(verdict="FAIL"))

        lineas = destino.read_text(encoding="utf-8").splitlines()
        assert len(lineas) == 2

        import json

        primero = json.loads(lineas[0])
        assert primero["verdict"] == "PASS"
        assert primero["domain"] == "codeforces"
