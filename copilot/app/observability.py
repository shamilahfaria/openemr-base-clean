"""Turn telemetry — STUB (no implementation yet).

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


class TelemetryExporter(Protocol):
    def export(self, telemetry: TurnTelemetry) -> None: ...


class LoggingExporter:
    """Structured-log backend: one INFO line per turn."""

    def export(self, telemetry: TurnTelemetry) -> None:
        logger.info("turn_telemetry %s", telemetry.model_dump_json())
