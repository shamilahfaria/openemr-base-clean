"""The in-memory session store."""
from __future__ import annotations

import pytest

from app.sessions import SessionPatientMismatch, SessionStore

SID = "session-1"
PATIENT = "uuid-pat-1"
OTHER_PATIENT = "uuid-pat-2"


class TestHistory:
    def test_new_session_has_empty_history(self):
        store = SessionStore()
        assert store.history(SID, PATIENT) == []

    def test_appended_messages_come_back_in_order(self):
        store = SessionStore()
        store.append(SID, PATIENT, "user", "how is her pain?")
        store.append(SID, PATIENT, "assistant", "Pain controlled overnight.")
        assert store.history(SID, PATIENT) == [
            {"role": "user", "content": "how is her pain?"},
            {"role": "assistant", "content": "Pain controlled overnight."},
        ]

    def test_sessions_are_isolated_from_each_other(self):
        store = SessionStore()
        store.append("session-a", PATIENT, "user", "message a")
        store.append("session-b", PATIENT, "user", "message b")
        assert store.history("session-a", PATIENT) == [
            {"role": "user", "content": "message a"}
        ]
        assert store.history("session-b", PATIENT) == [
            {"role": "user", "content": "message b"}
        ]

    def test_mutating_returned_history_does_not_affect_the_store(self):
        store = SessionStore()
        store.append(SID, PATIENT, "user", "original")
        leaked = store.history(SID, PATIENT)
        leaked.append({"role": "user", "content": "injected"})
        assert store.history(SID, PATIENT) == [
            {"role": "user", "content": "original"}
        ]


class TestPatientBinding:
    def test_history_with_different_patient_raises(self):
        store = SessionStore()
        store.append(SID, PATIENT, "user", "hello")
        with pytest.raises(SessionPatientMismatch):
            store.history(SID, OTHER_PATIENT)

    def test_append_with_different_patient_raises(self):
        store = SessionStore()
        store.append(SID, PATIENT, "user", "hello")
        with pytest.raises(SessionPatientMismatch):
            store.append(SID, OTHER_PATIENT, "user", "smuggled")

    def test_mismatch_does_not_leak_existing_history(self):
        store = SessionStore()
        store.append(SID, PATIENT, "user", "PHI for patient one")
        with pytest.raises(SessionPatientMismatch) as exc_info:
            store.history(SID, OTHER_PATIENT)
        assert "PHI for patient one" not in str(exc_info.value)


class TestClear:
    def test_clear_drops_the_session(self):
        store = SessionStore()
        store.append(SID, PATIENT, "user", "hello")
        store.clear(SID)
        assert store.history(SID, PATIENT) == []

    def test_cleared_session_can_be_rebound_to_a_new_patient(self):
        # Chart closed -> session gone -> the id may be reused for another chart.
        store = SessionStore()
        store.append(SID, PATIENT, "user", "hello")
        store.clear(SID)
        assert store.history(SID, OTHER_PATIENT) == []

    def test_clearing_unknown_session_is_a_no_op(self):
        store = SessionStore()
        store.clear("never-existed")  # must not raise
