"""
eon.telemetry
==============
Metrics recorder for EON kernel — counters, gauges, histograms and
snapshots. Zero external dependencies (no Prometheus, no OpenTelemetry).

Records:
- Plan scores and simulation confidence
- Verification confidence and layer status
- Memory hit rate (episodic search, skill match)
- Failure categories
- Replan count

Usage::

    from eon.telemetry import MetricsRecorder

    recorder = MetricsRecorder()
    recorder.increment("executions.total")
    recorder.gauge("plan.score", 0.85)
    recorder.histogram("verification.confidence", 0.92)
    snapshot = recorder.snapshot()
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HistogramData:
    """Histogram bucket data for a single metric."""

    count: int = 0
    sum: float = 0.0
    min: float = float("inf")
    max: float = float("-inf")
    buckets: list[float] = field(default_factory=list)

    def record(self, value: float) -> None:
        self.count += 1
        self.sum += value
        self.min = min(self.min, value)
        self.max = max(self.max, value)
        self.buckets.append(value)
        # Keep memory bounded
        if len(self.buckets) > 1000:
            self.buckets = self.buckets[-500:]

    @property
    def avg(self) -> float:
        return self.sum / self.count if self.count > 0 else 0.0

    @property
    def p50(self) -> float:
        return self._percentile(0.5)

    @property
    def p95(self) -> float:
        return self._percentile(0.95)

    @property
    def p99(self) -> float:
        return self._percentile(0.99)

    def _percentile(self, p: float) -> float:
        if not self.buckets:
            return 0.0
        sorted_vals = sorted(self.buckets)
        idx = int(len(sorted_vals) * p)
        idx = min(idx, len(sorted_vals) - 1)
        return sorted_vals[idx]

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "sum": round(self.sum, 4),
            "min": round(self.min, 4) if self.min != float("inf") else 0.0,
            "max": round(self.max, 4) if self.max != float("-inf") else 0.0,
            "avg": round(self.avg, 4),
            "p50": round(self.p50, 4),
            "p95": round(self.p95, 4),
            "p99": round(self.p99, 4),
        }


class MetricsRecorder:
    """Thread-safe metrics recorder.

    Supports:
    - ``increment(name, value=1)``: Counter
    - ``gauge(name, value)``: Gauge (latest value wins)
    - ``histogram(name, value)``: Histogram with percentiles

    All metrics are in-memory. ``snapshot()`` returns a dict suitable
    for JSON serialization.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, HistogramData] = defaultdict(HistogramData)
        self._start_time = time.monotonic()

    def increment(self, name: str, value: float = 1.0) -> None:
        """Increment a counter."""
        with self._lock:
            self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        """Set a gauge to a value."""
        with self._lock:
            self._gauges[name] = value

    def histogram(self, name: str, value: float) -> None:
        """Record a value in a histogram."""
        with self._lock:
            self._histograms[name].record(value)

    def get_counter(self, name: str) -> float:
        with self._lock:
            return self._counters.get(name, 0.0)

    def get_gauge(self, name: str) -> float:
        with self._lock:
            return self._gauges.get(name, 0.0)

    def get_histogram(self, name: str) -> HistogramData:
        with self._lock:
            return self._histograms.get(name, HistogramData())

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of all metrics."""
        with self._lock:
            uptime = time.monotonic() - self._start_time
            return {
                "uptime_seconds": round(uptime, 2),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {name: hist.to_dict() for name, hist in self._histograms.items()},
            }

    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._start_time = time.monotonic()


# Singleton instance for convenience
_default_recorder: MetricsRecorder | None = None


def get_default_recorder() -> MetricsRecorder:
    """Get or create the default global MetricsRecorder."""
    global _default_recorder
    if _default_recorder is None:
        _default_recorder = MetricsRecorder()
    return _default_recorder
