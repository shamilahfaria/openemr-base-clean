"""In-memory session store — STUB (no implementation yet).

Multi-turn state, scoped to a single patient chart session (ARCHITECTURE.md,
Component 4): one open chart = one session, held in memory, dropped at session
end. A session is BOUND to the patient it was created for — reusing its
``session_id`` with a different ``patient_id`` is a cross-chart leak and must
raise ``SessionPatientMismatch`` (fail closed).
"""
from __future__ import annotations


class SessionPatientMismatch(Exception):
    """A session_id was reused with a different patient's chart."""


class SessionStore:
    def __init__(self):
        raise NotImplementedError

    def history(self, session_id: str, patient_id: str) -> list[dict]:
        """Conversation so far (chronological ``{"role", "content"}`` dicts).

        Creates the session bound to ``patient_id`` on first use.
        """
        raise NotImplementedError

    def append(self, session_id: str, patient_id: str, role: str, content: str) -> None:
        """Append one message to the session's history."""
        raise NotImplementedError

    def clear(self, session_id: str) -> None:
        """Drop the session and its history (chart closed)."""
        raise NotImplementedError
