"""POST /chat — one conversation turn. STUB (no implementation yet).

Assembles the pipeline (ARCHITECTURE.md Request Flow): auth extraction ->
scope guard -> orchestrator -> verifier -> (fallback on failure) -> audit
event -> response. Providers are FastAPI dependencies so tests inject fakes;
production wiring constructs them from env config.

Contract:
  * ``Authorization: Bearer <token>`` and ``X-Clinician-Id`` required -> 401
    otherwise. The bearer never appears in any response.
  * Verified turn -> 200 {answer, citations, warnings, degraded: false,
    correlation_id} + a "verified" audit event.
  * Verification failure or orchestrator error -> 200 with the fallback
    answer (recent visit history), degraded: true, + "fallback" audit event.
  * Fallback failure too -> 503 (generic detail, no PHI) + "denied" audit event.
  * Session reused with a different patient -> 409.
  * ``correlation_id`` in the body matches the X-Correlation-ID header.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, field_validator

from .audit import AuditTrail, build_denial_event, build_turn_event
from .middleware import get_correlation_id
from .orchestrator import Orchestrator, TurnDraft
from .scope import PatientScopeGuard
from .sessions import SessionPatientMismatch
from .verifier import Citation, Verifier

router = APIRouter()

# Produces the safe fallback answer (recent visit history) for a patient.
FallbackFn = Callable[[str, str], Awaitable[str]]


class ChatRequest(BaseModel):
    patient_id: str
    message: str
    session_id: str

    @field_validator("patient_id", "message", "session_id")
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-blank")
        return value


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    warnings: list[str]
    degraded: bool
    correlation_id: str


def get_bearer_token(authorization: str = Header("")) -> str:
    """Extract the OAuth2 bearer token; 401 when missing/blank."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="bearer token required")
    return token.strip()


def get_clinician_id(x_clinician_id: str = Header("")) -> str:
    """The authenticated end-user for audit attribution; 401 when missing."""
    if not x_clinician_id.strip():
        raise HTTPException(status_code=401, detail="clinician identity required")
    return x_clinician_id.strip()


def get_orchestrator() -> Orchestrator:
    raise NotImplementedError  # production wiring; overridden in tests


def get_verifier() -> Verifier:
    raise NotImplementedError  # production wiring; overridden in tests


def get_audit_trail() -> AuditTrail:
    raise NotImplementedError  # production wiring; overridden in tests


def get_fallback_provider() -> FallbackFn:
    raise NotImplementedError  # production wiring; overridden in tests


@router.post("/chat")
async def chat(
    request: ChatRequest,
    bearer_token: str = Depends(get_bearer_token),
    clinician_id: str = Depends(get_clinician_id),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    verifier: Verifier = Depends(get_verifier),
    audit_trail: AuditTrail = Depends(get_audit_trail),
    fallback: FallbackFn = Depends(get_fallback_provider),
) -> ChatResponse:
    correlation_id = get_correlation_id()
    timestamp = datetime.now(timezone.utc)
    guard = PatientScopeGuard(request.patient_id)

    try:
        draft = await orchestrator.run_turn(
            patient_id=request.patient_id,
            bearer_token=bearer_token,
            session_id=request.session_id,
            message=request.message,
            scope_guard=guard,
        )
    except SessionPatientMismatch:
        raise HTTPException(
            status_code=409, detail="session is bound to a different patient"
        )
    except Exception:
        # Agent unavailable (model down, tool loop, upstream error) — take the
        # fallback path with an empty draft; details stay in server logs.
        draft = TurnDraft(answer="", retrieved=[], tools_used=[])

    result = verifier.verify(draft)

    def audit(outcome_result) -> None:
        audit_trail.record(
            build_turn_event(
                correlation_id=correlation_id,
                clinician_id=clinician_id,
                patient_id=request.patient_id,
                timestamp=timestamp,
                message=request.message,
                draft=draft,
                result=outcome_result,
                model=getattr(orchestrator, "_model", "unknown"),
            )
        )

    if result.passed:
        audit(result)
        return ChatResponse(
            answer=result.answer,
            citations=result.citations,
            warnings=result.warnings,
            degraded=False,
            correlation_id=correlation_id,
        )

    # Verification failed (or the agent errored): safe fallback.
    try:
        fallback_answer = await fallback(request.patient_id, bearer_token)
    except Exception:
        audit_trail.record(
            build_denial_event(
                correlation_id=correlation_id,
                clinician_id=clinician_id,
                patient_id=request.patient_id,
                timestamp=timestamp,
                reason="agent and fallback both unavailable",
            )
        )
        raise HTTPException(status_code=503, detail="service temporarily unavailable")

    audit(result)
    return ChatResponse(
        answer=fallback_answer,
        citations=[],
        warnings=result.warnings,
        degraded=True,
        correlation_id=correlation_id,
    )
