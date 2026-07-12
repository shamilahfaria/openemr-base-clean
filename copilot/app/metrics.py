"""In-process metrics for the live dashboard (GET /metrics, GET /dashboard).

PHI-free by construction: the registry stores only what TurnTelemetry carries
(correlation id, outcome, tool names, latency, tokens, cost) plus per-request
status counts from the middleware — never a patient id, clinician id, message,
or answer. It backs the assignment's dashboard requirement (request count,
error count, p50/p95 latency, tool calls, verification pass/fail) without an
external service, so the deployed instance is self-contained.

State is per-process and resets on deploy; alert thresholds over these
numbers are documented in copilot/OBSERVABILITY.md.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Deque

from .observability import TurnTelemetry

# Bound memory: percentiles are computed over the most recent turns.
LATENCY_WINDOW = 500
RECENT_TURNS = 25


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = round(fraction * (len(sorted_values) - 1))
    return sorted_values[index]


class MetricsRegistry:
    """Aggregates request- and turn-level metrics for one process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.time()
        self._requests_total = 0
        self._responses_by_class: Counter[str] = Counter()   # "2xx", "4xx", ...
        self._turn_outcomes: Counter[str] = Counter()        # verified/fallback/denied
        self._tool_calls: Counter[str] = Counter()
        self._latencies_ms: Deque[float] = deque(maxlen=LATENCY_WINDOW)
        self._recent_turns: Deque[dict] = deque(maxlen=RECENT_TURNS)
        self._verification_passed = 0
        self._verification_failed = 0
        self._warnings_total = 0
        self._withheld_total = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cost_usd = 0.0

    def record_request(self, status_code: int) -> None:
        """Count every /chat HTTP response, including 4xx rejected requests."""
        with self._lock:
            self._requests_total += 1
            self._responses_by_class[f"{status_code // 100}xx"] += 1

    def record_turn(self, telemetry: TurnTelemetry) -> None:
        with self._lock:
            self._turn_outcomes[telemetry.outcome] += 1
            self._tool_calls.update(telemetry.tools_used)
            self._latencies_ms.append(telemetry.latency_ms)
            if telemetry.verification_passed:
                self._verification_passed += 1
            else:
                self._verification_failed += 1
            self._warnings_total += telemetry.warnings_count
            self._withheld_total += telemetry.withheld_count
            self._input_tokens += telemetry.input_tokens
            self._output_tokens += telemetry.output_tokens
            self._cost_usd += telemetry.cost_usd
            self._recent_turns.appendleft(
                {
                    "correlation_id": telemetry.correlation_id,
                    "outcome": telemetry.outcome,
                    "latency_ms": round(telemetry.latency_ms),
                    "tools_used": list(telemetry.tools_used),
                    "warnings": telemetry.warnings_count,
                    "withheld": telemetry.withheld_count,
                    "tokens": telemetry.input_tokens + telemetry.output_tokens,
                    "cost_usd": round(telemetry.cost_usd, 5),
                    "at": time.time(),
                }
            )

    def snapshot(self) -> dict:
        with self._lock:
            latencies = sorted(self._latencies_ms)
            turns = sum(self._turn_outcomes.values())
            errors = (
                self._responses_by_class["4xx"] + self._responses_by_class["5xx"]
            )
            return {
                "uptime_seconds": round(time.time() - self._started),
                "requests_total": self._requests_total,
                "responses_by_class": dict(self._responses_by_class),
                "errors_total": errors,
                "error_rate": round(errors / self._requests_total, 4)
                if self._requests_total
                else 0.0,
                "turns_total": turns,
                "turn_outcomes": dict(self._turn_outcomes),
                "latency_ms": {
                    "p50": round(_percentile(latencies, 0.50)),
                    "p95": round(_percentile(latencies, 0.95)),
                    "p99": round(_percentile(latencies, 0.99)),
                    "window": len(latencies),
                },
                "verification": {
                    "passed": self._verification_passed,
                    "failed": self._verification_failed,
                    "pass_rate": round(
                        self._verification_passed
                        / (self._verification_passed + self._verification_failed),
                        4,
                    )
                    if (self._verification_passed + self._verification_failed)
                    else 0.0,
                    "warnings_total": self._warnings_total,
                    "withheld_total": self._withheld_total,
                },
                "tool_calls": dict(self._tool_calls),
                "tokens": {
                    "input": self._input_tokens,
                    "output": self._output_tokens,
                },
                "cost_usd_total": round(self._cost_usd, 4),
                "recent_turns": list(self._recent_turns),
            }


class MetricsExporter:
    """TelemetryExporter backend that feeds the in-process registry."""

    def __init__(self, registry: MetricsRegistry):
        self._registry = registry

    def export(self, telemetry: TurnTelemetry) -> None:
        self._registry.record_turn(telemetry)


_registry: MetricsRegistry | None = None


def get_registry() -> MetricsRegistry:
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry


def reset_registry() -> None:
    """Tests only."""
    global _registry
    _registry = None
