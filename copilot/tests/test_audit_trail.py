"""
TDD (Red) suite — build step 7b: the HIPAA audit trail (AUDIT C2).

Pins: clinician-attributed immutable events, PHI manifest as source_ids only
(never record bodies), append-only trail surface, caller-supplied timestamps,
denial events for fail-closed paths.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.audit import AuditTrail, build_denial_event, build_turn_event
from app.orchestrator import TurnDraft
from app.tools.chart import MedicationRecord
from app.verifier import Citation, VerificationResult

NOW = datetime(2026, 7, 9, 14, 30, tzinfo=timezone.utc)
CORR = "corr-123"
CLINICIAN = "nurse-maria"
PATIENT = "uuid-pat-1"

MORPHINE = MedicationRecord(
    source_id="med-1", name="Morphine sulfate", dose="5 mg", route="IV",
    sig="5 mg IV q4h PRN", is_prn=True, prn_interval="Q4H", status="active",
)

DRAFT = TurnDraft(
    answer="On morphine [src: med-1].",
    retrieved=[MORPHINE],
    tools_used=["get_medications"],
)

PASSED_RESULT = VerificationResult(
    answer="On morphine.",
    citations=[Citation(claim="On morphine.", source_id="med-1")],
    warnings=["one warning"],
    withheld=[],
    rules_version="2026.07.0",
)

FAILED_RESULT = VerificationResult(
    answer="",
    citations=[],
    warnings=["2 statement(s) were withheld"],
    withheld=["claim one", "claim two"],
    rules_version="2026.07.0",
)


def turn_event(result=PASSED_RESULT, **overrides):
    kwargs = dict(
        correlation_id=CORR,
        clinician_id=CLINICIAN,
        patient_id=PATIENT,
        timestamp=NOW,
        message="how is her pain?",
        draft=DRAFT,
        result=result,
        model="claude-sonnet-4-5",
    )
    kwargs.update(overrides)
    return build_turn_event(**kwargs)


class TestTurnEvents:
    def test_event_attributes_the_clinician_patient_and_request(self):
        event = turn_event()
        assert event.correlation_id == CORR
        assert event.clinician_id == CLINICIAN
        assert event.patient_id == PATIENT
        assert event.timestamp == NOW
        assert event.message == "how is her pain?"
        assert event.model == "claude-sonnet-4-5"

    def test_verified_outcome_when_verification_passed(self):
        assert turn_event().outcome == "verified"

    def test_fallback_outcome_when_verification_failed(self):
        assert turn_event(result=FAILED_RESULT).outcome == "fallback"

    def test_phi_manifest_lists_disclosed_source_ids(self):
        assert turn_event().phi_manifest == ["med-1"]

    def test_phi_manifest_never_contains_record_bodies(self):
        # References only — the manifest proves minimum-necessary disclosure
        # without duplicating PHI into the audit store.
        serialized = turn_event().model_dump_json()
        assert "med-1" in serialized
        assert "Morphine" not in serialized
        assert "5 mg" not in serialized

    def test_tools_and_verification_counts_captured(self):
        event = turn_event(result=FAILED_RESULT)
        assert event.tools_used == ["get_medications"]
        assert event.warnings_count == 1
        assert event.withheld_count == 2

    def test_manifest_deduplicates_source_ids(self):
        draft = TurnDraft(
            answer="x", retrieved=[MORPHINE, MORPHINE], tools_used=["get_medications"]
        )
        assert turn_event(draft=draft).phi_manifest == ["med-1"]


class TestDenialEvents:
    def test_denial_event_captures_reason_and_outcome(self):
        event = build_denial_event(
            correlation_id=CORR,
            clinician_id=CLINICIAN,
            patient_id=PATIENT,
            timestamp=NOW,
            reason="cross-patient tool call denied",
        )
        assert event.outcome == "denied"
        assert event.reason == "cross-patient tool call denied"
        assert event.phi_manifest == []      # nothing was disclosed
        assert event.tools_used == []


class TestEventIntegrity:
    def test_blank_clinician_id_is_rejected(self):
        with pytest.raises((ValidationError, ValueError)):
            turn_event(clinician_id="  ")

    def test_blank_patient_id_is_rejected(self):
        with pytest.raises((ValidationError, ValueError)):
            turn_event(patient_id="")

    def test_blank_correlation_id_is_rejected(self):
        with pytest.raises((ValidationError, ValueError)):
            turn_event(correlation_id="")

    def test_events_are_immutable_once_built(self):
        event = turn_event()
        with pytest.raises((ValidationError, TypeError)):
            event.outcome = "tampered"


class TestAppendOnlyTrail:
    def test_recorded_events_come_back_in_order(self):
        trail = AuditTrail()
        first = turn_event()
        second = build_denial_event(
            correlation_id="corr-456",
            clinician_id=CLINICIAN,
            patient_id=PATIENT,
            timestamp=NOW,
            reason="denied",
        )
        trail.record(first)
        trail.record(second)
        assert trail.events() == [first, second]

    def test_returned_list_is_a_defensive_copy(self):
        trail = AuditTrail()
        trail.record(turn_event())
        leaked = trail.events()
        leaked.clear()
        assert len(trail.events()) == 1

    @pytest.mark.parametrize("forbidden", ["update", "delete", "remove", "clear", "pop"])
    def test_trail_exposes_no_mutation_methods(self, forbidden):
        # Append-only by construction: the public surface cannot rewrite history.
        assert not hasattr(AuditTrail(), forbidden)
