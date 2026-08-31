"""
eon.codeforces.trace_logger
==============================
Logger de traces verificados: cada intento del solver (PASS o FAIL) se
serializa en un TraceRecord y se escribe a un TraceSink. Pensado para
construir un dataset de entrenamiento/preferencias -- los FAIL importan
tanto como los PASS, no se descartan.

No depende de nada del kernel (ni siquiera de eon.sandbox/eon.tools/
eon.verification, aunque están permitidos para eon/codeforces/): solo
dataclasses planas + stdlib. eon.codeforces.solver es quien traduce sus
propios tipos (SandboxResult, LayerResult, etc.) a estos antes de
llamar sink.write() -- ver eon/codeforces/solver.py.

El kernel nunca importa este módulo (regla verificada en
eon/tests/test_codeforces_boundary.py).
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ModelInfo:
    name: str = ""
    provider: str = ""
    temperature: float | None = None


@dataclass(frozen=True)
class SolutionInfo:
    code: str
    lang: str


@dataclass(frozen=True)
class VerifierInfo:
    name: str
    mode: str


@dataclass(frozen=True)
class SampleResult:
    index: int
    passed: bool
    motivo: str


@dataclass(frozen=True)
class SandboxInfo:
    exit_code: int | None
    timed_out: bool
    duration_s: float


@dataclass(frozen=True)
class TraceRecord:
    """Un intento de resolución, verificado. `id` es un hash del resto
    del contenido (todo salvo `id`/`ts`) -- dos intentos con el mismo
    contenido dan el mismo id, útil para deduplicar. Constrúyelo con
    `TraceRecord.create(...)`, no con el constructor directo, para que
    `id` se calcule correctamente."""

    id: str
    ts: str
    domain: str
    problem_id: str
    objective: str
    prompt: str
    model: ModelInfo
    output_raw: str
    solution: SolutionInfo | None
    attempt_index: int
    verdict: str  # "PASS" | "FAIL"
    verifier: VerifierInfo
    sample_results: list[SampleResult] = field(default_factory=list)
    sandbox: SandboxInfo | None = None
    rating: int | None = None
    error: str | None = None

    @classmethod
    def create(
        cls,
        *,
        domain: str,
        problem_id: str,
        objective: str,
        prompt: str,
        model: ModelInfo,
        output_raw: str,
        solution: SolutionInfo | None,
        attempt_index: int,
        verdict: str,
        verifier: VerifierInfo,
        sample_results: list[SampleResult] | None = None,
        sandbox: SandboxInfo | None = None,
        rating: int | None = None,
        error: str | None = None,
        ts: str | None = None,
    ) -> TraceRecord:
        borrador = cls(
            id="",
            ts=ts or datetime.now(UTC).isoformat(),
            domain=domain,
            problem_id=problem_id,
            objective=objective,
            prompt=prompt,
            model=model,
            output_raw=output_raw,
            solution=solution,
            attempt_index=attempt_index,
            verdict=verdict,
            verifier=verifier,
            sample_results=sample_results or [],
            sandbox=sandbox,
            rating=rating,
            error=error,
        )
        return replace(borrador, id=_content_hash(borrador))


def _content_dict(record: TraceRecord) -> dict:
    """Serializa `record` a dict plano, sin `id` ni `ts` (cambian entre
    escrituras del mismo contenido lógico, no deben afectar el hash)."""
    d = asdict(record)
    d.pop("id", None)
    d.pop("ts", None)
    return d


def _content_hash(record: TraceRecord) -> str:
    # sort_keys=True fija el orden de claves explícitamente (no depende
    # del orden de declaración de los dataclasses) para que el hash sea
    # reproducible entre procesos/versiones.
    payload = json.dumps(_content_dict(record), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TraceSink(Protocol):
    def write(self, record: TraceRecord) -> None: ...


class JsonlTraceSink:
    """TraceSink append-only a un archivo .jsonl -- un TraceRecord en
    JSON por línea. Crea el directorio destino si no existe.

    Lock básico (threading.Lock) para escrituras concurrentes desde
    threads del mismo proceso -- no coordina entre procesos distintos.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: TraceRecord) -> None:
        linea = json.dumps(asdict(record), sort_keys=True, ensure_ascii=True)
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
