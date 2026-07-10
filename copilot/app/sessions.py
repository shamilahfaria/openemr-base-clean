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
        self._sessions: dict[str, dict] = {}

    def _session(self, session_id: str, patient_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            session = {"patient_id": patient_id, "messages": []}
            self._sessions[session_id] = session
        elif session["patient_id"] != patient_id:
            # Never include existing history in the error — it is another
            # patient's PHI.
            raise SessionPatientMismatch(
                f"session '{session_id}' is bound to a different patient"
            )
        return session

    def history(self, session_id: str, patient_id: str) -> list[dict]:
        """Conversation so far (chronological ``{"role", "content"}`` dicts).

        Creates the session bound to ``patient_id`` on first use. Returns a
        defensive copy — callers cannot mutate the store.
        """
        return [dict(m) for m in self._session(session_id, patient_id)["messages"]]

    def append(self, session_id: str, patient_id: str, role: str, content: str) -> None:
        """Append one message to the session's history."""
        self._session(session_id, patient_id)["messages"].append(
            {"role": role, "content": content}
        )

    def clear(self, session_id: str) -> None:
        """Drop the session and its history (chart closed). Unknown id is a no-op."""
        self._sessions.pop(session_id, None)
