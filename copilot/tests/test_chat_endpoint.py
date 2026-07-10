"""
TDD (Red) suite — build step 8: POST /chat assembly + fallback.

Fake orchestrator + fallback provider; REAL verifier and audit trail — this is
the integration seam, so the deterministic pieces run for real.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditTrail
from app.chat import (
    get_audit_trail,
    get_bearer_token,
    get_clinician_id,
    get_fallback_provider,
    get_orchestrator,
    get_verifier,
)
from app.main import create_app
from app.middleware import CORRELATION_HEADER
from app.orchestrator import ToolLoopLimitError, TurnDraft
from app.sessions import SessionPatientMismatch
from app.tools.chart import MedicationRecord
from app.verifier import ClinicalRuleSet, Verifier

PATIENT = "uuid-pat-1"
TOKEN = "test-bearer-token-123"
CLINICIAN = "nurse-maria"
FALLBACK_TEXT = "Most recent visit: 2026-07-01 hospice admission."

MORPHINE = MedicationRecord(
    source_id="med-1", name="Morphine sulfate", dose="5 mg", route="IV",
    sig="5 mg IV q4h PRN", is_prn=True, prn_interval="Q4H", status="active",
)

VERIFIED_DRAFT = TurnDraft(
    answer="She is on morphine 5 mg [src: med-1].",
    retrieved=[MORPHINE],
    tools_used=["get_medications"],
)

UNCITED_DRAFT = TurnDraft(
    answer="Her prognosis is weeks to months.",  # uncited -> withheld -> fallback
    retrieved=[MORPHINE],
    tools_used=["get_medications"],
)


class FakeOrchestrator:
    def __init__(self, result: object):
        self._result = result
        self.calls: list[dict] = []

    async def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeFallback:
    def __init__(self, result: object = FALLBACK_TEXT):
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, patient_id: str, bearer_token: str) -> str:
        self.calls.append((patient_id, bearer_token))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def harness():
    """App with fakes injected; returns (client, orchestrator setter, trail, fallback)."""
    app = create_app()
    state = {
        "orchestrator": FakeOrchestrator(VERIFIED_DRAFT),
        "fallback": FakeFallback(),
        "trail": AuditTrail(),
    }
    app.dependency_overrides[get_orchestrator] = lambda: state["orchestrator"]
    app.dependency_overrides[get_verifier] = lambda: Verifier(
        ClinicalRuleSet(version="test-rules")
    )
    app.dependency_overrides[get_audit_trail] = lambda: state["trail"]
    app.dependency_overrides[get_fallback_provider] = lambda: state["fallback"]
    client = TestClient(app, raise_server_exceptions=False)
    return client, state


def post_chat(client, message="how is her pain?", headers: dict | None = None):
    base_headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Clinician-Id": CLINICIAN,
    }
    if headers is not None:
        base_headers = headers
    return client.post(
        "/chat",
        json={"patient_id": PATIENT, "message": message, "session_id": "session-1"},
        headers=base_headers,
    )


class TestAuth:
    def test_missing_authorization_header_is_401(self, harness):
        client, _ = harness
        response = post_chat(client, headers={"X-Clinician-Id": CLINICIAN})
        assert response.status_code == 401

    def test_blank_bearer_is_401(self, harness):
        client, _ = harness
        response = post_chat(
            client,
            headers={"Authorization": "Bearer   ", "X-Clinician-Id": CLINICIAN},
        )
        assert response.status_code == 401

    def test_missing_clinician_header_is_401(self, harness):
        client, _ = harness
        response = post_chat(client, headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 401

    def test_bearer_token_never_appears_in_response(self, harness):
        client, _ = harness
        response = post_chat(client)
        assert TOKEN not in response.text


class TestValidation:
    def test_blank_patient_id_is_422(self, harness):
        client, _ = harness
        response = client.post(
            "/chat",
            json={"patient_id": " ", "message": "hi", "session_id": "s"},
            headers={"Authorization": f"Bearer {TOKEN}", "X-Clinician-Id": CLINICIAN},
        )
        assert response.status_code == 422

    def test_blank_message_is_422(self, harness):
        client, _ = harness
        response = post_chat(client, message="   ")
        assert response.status_code == 422


class TestVerifiedTurn:
    def test_200_with_cited_answer_and_not_degraded(self, harness):
        client, _ = harness
        response = post_chat(client)
        assert response.status_code == 200
        body = response.json()
        assert "morphine 5 mg" in body["answer"]
        assert body["degraded"] is False
        assert body["citations"] == [
            {"claim": "She is on morphine 5 mg.", "source_id": "med-1"}
        ]

    def test_correlation_id_in_body_matches_header(self, harness):
        client, _ = harness
        response = post_chat(client)
        assert response.json()["correlation_id"] == response.headers[CORRELATION_HEADER]

    def test_orchestrator_receives_request_context(self, harness):
        client, state = harness
        post_chat(client, message="any allergies?")
        (call,) = state["orchestrator"].calls
        assert call["patient_id"] == PATIENT
        assert call["bearer_token"] == TOKEN
        assert call["session_id"] == "session-1"
        assert call["message"] == "any allergies?"
        assert call["scope_guard"] is not None

    def test_verified_audit_event_recorded(self, harness):
        client, state = harness
        post_chat(client)
        (event,) = state["trail"].events()
        assert event.outcome == "verified"
        assert event.clinician_id == CLINICIAN
        assert event.patient_id == PATIENT
        assert event.phi_manifest == ["med-1"]
        assert event.tools_used == ["get_medications"]


class TestFallback:
    def test_verification_failure_returns_fallback_degraded(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(UNCITED_DRAFT)
        response = post_chat(client)
        assert response.status_code == 200
        body = response.json()
        assert body["degraded"] is True
        assert body["answer"] == FALLBACK_TEXT
        assert body["citations"] == []

    def test_fallback_path_keeps_verifier_warnings(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(UNCITED_DRAFT)
        body = post_chat(client).json()
        assert any("withheld" in w for w in body["warnings"])

    def test_fallback_audit_event_recorded(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(UNCITED_DRAFT)
        post_chat(client)
        (event,) = state["trail"].events()
        assert event.outcome == "fallback"

    @pytest.mark.parametrize(
        "error", [RuntimeError("anthropic down"), ToolLoopLimitError("looped")]
    )
    def test_orchestrator_error_returns_fallback_degraded(self, harness, error):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(error)
        response = post_chat(client)
        assert response.status_code == 200
        body = response.json()
        assert body["degraded"] is True
        assert body["answer"] == FALLBACK_TEXT

    def test_fallback_receives_patient_and_bearer(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(RuntimeError("down"))
        post_chat(client)
        assert state["fallback"].calls == [(PATIENT, TOKEN)]


class TestTotalFailure:
    def test_fallback_failure_is_503_with_correlation_id(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(RuntimeError("down"))
        state["fallback"] = FakeFallback(RuntimeError("openemr down too"))
        response = post_chat(client)
        assert response.status_code == 503
        assert CORRELATION_HEADER in response.headers
        # generic detail only — no internals, no PHI
        assert "openemr down too" not in response.text

    def test_total_failure_records_denied_audit_event(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(RuntimeError("down"))
        state["fallback"] = FakeFallback(RuntimeError("openemr down too"))
        post_chat(client)
        (event,) = state["trail"].events()
        assert event.outcome == "denied"


class TestSessionConflict:
    def test_session_reused_for_another_patient_is_409(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(SessionPatientMismatch("bound"))
        response = post_chat(client)
        assert response.status_code == 409
