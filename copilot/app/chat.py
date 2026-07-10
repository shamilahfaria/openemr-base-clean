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

from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from .audit import AuditTrail
from .orchestrator import Orchestrator
from .verifier import Citation, Verifier

router = APIRouter()

# Produces the safe fallback answer (recent visit history) for a patient.
FallbackFn = Callable[[str, str], Awaitable[str]]


class ChatRequest(BaseModel):
    patient_id: str
    message: str
    session_id: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    warnings: list[str]
    degraded: bool
    correlation_id: str


def get_bearer_token(authorization: str = Header("")) -> str:
    """Extract the OAuth2 bearer token; 401 when missing/blank."""
    raise NotImplementedError


def get_clinician_id(x_clinician_id: str = Header("")) -> str:
    """The authenticated end-user for audit attribution; 401 when missing."""
    raise NotImplementedError


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
    raise NotImplementedError
