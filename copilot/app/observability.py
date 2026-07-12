"""Turn telemetry.

ARCHITECTURE.md Component 9 / AUDIT R3: observability is real from the first
request, and the telemetry that leaves the request path is PHI-FREE BY
CONSTRUCTION — correlation id, outcome, tool names, verification stats,
latency, model. No patient id. No message text. The patient-linked record
lives in the HIPAA audit trail (app/audit.py), inside the trust boundary.

``LoggingExporter`` emits structured logs (the MVP backend); a Langfuse
exporter drops in behind the same ``TelemetryExporter`` seam once keys exist.
Export failures must never break a request.
"""
from __future__ import annotations

import logging
from typing import Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# List pricing used for per-turn cost accounting (matches COST_ANALYSIS.md).
PRICE_PER_MTOK_INPUT_USD = 3.0
PRICE_PER_MTOK_OUTPUT_USD = 15.0


def turn_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * PRICE_PER_MTOK_INPUT_USD
        + output_tokens * PRICE_PER_MTOK_OUTPUT_USD
    ) / 1_000_000


class TurnTelemetry(BaseModel):
    """One /chat turn's metrics. Deliberately has NO patient/message fields."""

    correlation_id: str
    outcome: str                # "verified" | "fallback" | "denied"
    degraded: bool
    tools_used: list[str]
    verification_passed: bool
    warnings_count: int
    withheld_count: int
    latency_ms: float
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class TelemetryExporter(Protocol):
    def export(self, telemetry: TurnTelemetry) -> None: ...


class LoggingExporter:
    """Structured-log backend: one INFO line per turn."""

    def export(self, telemetry: TurnTelemetry) -> None:
        logger.info("turn_telemetry %s", telemetry.model_dump_json())


class CompositeExporter:
    """Fan out one turn's telemetry to several backends; one backend failing
    must not starve the others (each export is isolated)."""

    def __init__(self, exporters: list[TelemetryExporter]):
        self._exporters = list(exporters)

    def export(self, telemetry: TurnTelemetry) -> None:
        for exporter in self._exporters:
            try:
                exporter.export(telemetry)
            except Exception as exc:
                logger.warning(
                    "telemetry backend failed: %s", type(exc).__name__
                )
