"""HIPAA audit trail.

ARCHITECTURE.md Component 10 / AUDIT.md C2: OpenEMR attributes API reads to
the OAuth service account and never records the onward disclosure to the LLM,
so the sidecar owns its own compliance audit chain.

Contract:
  * One ``AuditEvent`` per request outcome, attributing the access to the
    authenticated CLINICIAN (never the service account) and the concrete
    patient. Blank clinician/patient/correlation ids are rejected.
  * The PHI-to-LLM manifest lists the ``source_id``s disclosed to the model —
    references only, never record bodies (minimum-necessary proof).
  * Events are immutable once built; the trail is append-only (record/read,
    no update/delete/clear).
  * Timestamps are supplied by the caller — the trail never invents time.
  * Denials (scope violations, fail-closed events) are recorded too.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from .orchestrator import TurnDraft
from .verifier import VerificationResult


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    correlation_id: str
    clinician_id: str           # the authenticated end-user (AUDIT C2)
    patient_id: str
    timestamp: datetime
    message: str | None         # the nurse's query (stored in-boundary)
    tools_used: list[str]
    phi_manifest: list[str]     # source_ids disclosed to the LLM — never bodies
    model: str                  # proves BAA-covered routing
    outcome: str                # "verified" | "fallback" | "denied"
    warnings_count: int
    withheld_count: int
    reason: str | None          # populated for denials

    @field_validator("correlation_id", "clinician_id", "patient_id")
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("audit attribution ids must be non-blank")
        return value


class AuditTrail:
    """Append-only, in-memory (swap for a durable WORM/SIEM store at scale)."""

    def __init__(self):
        self._events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    def events(self) -> list[AuditEvent]:
        return list(self._events)


def build_turn_event(
    *,
    correlation_id: str,
    clinician_id: str,
    patient_id: str,
    timestamp: datetime,
    message: str,
    draft: TurnDraft,
    result: VerificationResult,
    model: str,
) -> AuditEvent:
    """Audit event for a completed turn (verified or fallback)."""
    manifest = sorted(
        {r.source_id for r in draft.retrieved if hasattr(r, "source_id")}
    )
    return AuditEvent(
        correlation_id=correlation_id,
        clinician_id=clinician_id,
        patient_id=patient_id,
        timestamp=timestamp,
        message=message,
        tools_used=list(draft.tools_used),
        phi_manifest=manifest,
        model=model,
        outcome="verified" if result.passed else "fallback",
        warnings_count=len(result.warnings),
        withheld_count=len(result.withheld),
        reason=None,
    )


def build_denial_event(
    *,
    correlation_id: str,
    clinician_id: str,
    patient_id: str,
    timestamp: datetime,
    reason: str,
) -> AuditEvent:
    """Audit event for a fail-closed denial (scope violation, auth failure)."""
    return AuditEvent(
        correlation_id=correlation_id,
        clinician_id=clinician_id,
        patient_id=patient_id,
        timestamp=timestamp,
        message=None,
        tools_used=[],
        phi_manifest=[],
        model="",
        outcome="denied",
        warnings_count=0,
        withheld_count=0,
        reason=reason,
    )
