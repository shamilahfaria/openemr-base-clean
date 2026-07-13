"""
The Patient Scope Guard (AUDIT S1).

OpenEMR enforces no patient-level authorization for a clinical-user token, so
this guard IS the patient-level trust boundary. These tests pin:

  * construction requires a real active patient id (fail closed)
  * matching tool calls pass; anything else raises ScopeViolation
  * missing/blank patient_id argument is a violation (fail closed, not open)
  * ALL patient-referencing keys are checked (patient_id, patient, pid, subject)
  * exact match after strip — no case-folding
  * violations are logged (deny AND log); clean calls log nothing
"""
from __future__ import annotations

import logging

import pytest

from app.scope import PatientScopeGuard, ScopeViolation

ACTIVE = "uuid-active-patient"
OTHER = "uuid-other-patient"


class TestConstruction:
    def test_blank_active_patient_id_is_rejected(self):
        with pytest.raises(ValueError):
            PatientScopeGuard("")

    def test_whitespace_active_patient_id_is_rejected(self):
        with pytest.raises(ValueError):
            PatientScopeGuard("   ")


class TestMatchingCallsPass:
    def test_tool_call_scoped_to_active_patient_passes(self):
        guard = PatientScopeGuard(ACTIVE)
        # must not raise
        guard.validate_tool_call("get_patient_summary", {"patient_id": ACTIVE})

    @pytest.mark.parametrize(
        "tool_name",
        [
            "get_patient_summary",
            "get_medications",
            "get_allergies",
            "get_goals_of_care",
            "search_notes",
        ],
    )
    def test_every_tool_passes_with_matching_patient_id(self, tool_name):
        guard = PatientScopeGuard(ACTIVE)
        guard.validate_tool_call(tool_name, {"patient_id": ACTIVE})

    def test_extra_non_patient_arguments_are_ignored(self):
        guard = PatientScopeGuard(ACTIVE)
        guard.validate_tool_call(
            "search_notes", {"patient_id": ACTIVE, "query": "pain overnight"}
        )

    def test_whitespace_padded_matching_id_passes(self):
        guard = PatientScopeGuard(ACTIVE)
        guard.validate_tool_call("get_patient_summary", {"patient_id": f"  {ACTIVE}  "})

    def test_secondary_patient_key_matching_active_passes(self):
        guard = PatientScopeGuard(ACTIVE)
        guard.validate_tool_call(
            "get_labs", {"patient_id": ACTIVE, "patient": ACTIVE}
        )


class TestCrossPatientViolations:
    def test_different_patient_id_raises_scope_violation(self):
        guard = PatientScopeGuard(ACTIVE)
        with pytest.raises(ScopeViolation):
            guard.validate_tool_call("get_patient_summary", {"patient_id": OTHER})

    def test_missing_patient_id_argument_raises_scope_violation(self):
        # Fail closed: an unscoped tool call is never allowed through.
        guard = PatientScopeGuard(ACTIVE)
        with pytest.raises(ScopeViolation):
            guard.validate_tool_call("get_medications", {})

    def test_blank_patient_id_argument_raises_scope_violation(self):
        guard = PatientScopeGuard(ACTIVE)
        with pytest.raises(ScopeViolation):
            guard.validate_tool_call("get_medications", {"patient_id": "  "})

    def test_case_variant_of_active_id_is_rejected(self):
        # Exact match only — no case-folding on identifiers.
        guard = PatientScopeGuard(ACTIVE)
        with pytest.raises(ScopeViolation):
            guard.validate_tool_call(
                "get_patient_summary", {"patient_id": ACTIVE.upper()}
            )

    @pytest.mark.parametrize("ref_key", ["patient", "pid", "subject"])
    def test_any_patient_referencing_key_naming_another_patient_is_rejected(
        self, ref_key
    ):
        # Even when patient_id matches, a secondary reference to another
        # patient is a cross-patient attempt (e.g. smuggled search param).
        guard = PatientScopeGuard(ACTIVE)
        with pytest.raises(ScopeViolation):
            guard.validate_tool_call(
                "get_labs", {"patient_id": ACTIVE, ref_key: OTHER}
            )


class TestViolationLogging:
    def test_violation_is_logged_with_tool_name(self, caplog):
        guard = PatientScopeGuard(ACTIVE)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(ScopeViolation):
                guard.validate_tool_call("get_medications", {"patient_id": OTHER})
        assert any(
            record.levelno >= logging.WARNING and "get_medications" in record.getMessage()
            for record in caplog.records
        )

    def test_successful_validation_logs_nothing(self, caplog):
        guard = PatientScopeGuard(ACTIVE)
        with caplog.at_level(logging.DEBUG):
            guard.validate_tool_call("get_patient_summary", {"patient_id": ACTIVE})
        assert caplog.records == []
