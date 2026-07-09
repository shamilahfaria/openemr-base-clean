"""Patient Scope Guard — STUB (no implementation yet).

The audit found OpenEMR enforces NO patient-level authorization for a
clinical-user token (AUDIT.md S1): a nurse's token can read any patient her
role permits. Patient-level scoping is therefore the sidecar's own trust
boundary (ARCHITECTURE.md, Component 3).

Contract:
  * One guard per request, bound to the active chart's ``patient_id``.
    A blank active patient id is a configuration error (fail closed).
  * ``validate_tool_call(tool_name, arguments)`` must be called before every
    tool execution. It raises ``ScopeViolation`` when:
      - any patient-referencing argument (``patient_id``, ``patient``,
        ``pid``, ``subject``) names a different patient, or
      - the ``patient_id`` argument is missing or blank (tools must be
        explicitly scoped — fail closed, never fail open).
  * Matching is exact string equality after whitespace strip — no
    case-folding, no fuzzy matching.
  * Every violation is logged (deny AND log — Failure Modes table);
    successful validations log nothing.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Argument names that reference a patient. Any of these naming a different
# patient than the active chart is a cross-patient access attempt.
PATIENT_REF_KEYS = ("patient_id", "patient", "pid", "subject")


class ScopeViolation(Exception):
    """A tool call attempted to reach outside the active patient's chart."""


class PatientScopeGuard:
    """Hard-scopes every tool call to the active patient (AUDIT S1)."""

    def __init__(self, active_patient_id: str):
        if not active_patient_id or not active_patient_id.strip():
            raise ValueError("active patient id is required")
        self._active = active_patient_id.strip()

    def validate_tool_call(self, tool_name: str, arguments: dict) -> None:
        """Raise ``ScopeViolation`` unless ``arguments`` is scoped to the active patient."""
        patient_id = str(arguments.get("patient_id") or "").strip()
        if not patient_id:
            self._deny(tool_name, "missing or blank patient_id")
        if patient_id != self._active:
            self._deny(tool_name, "patient_id names a different patient")

        for key in PATIENT_REF_KEYS:
            if key == "patient_id" or key not in arguments:
                continue
            if str(arguments[key] or "").strip() != self._active:
                self._deny(tool_name, f"'{key}' names a different patient")

    def _deny(self, tool_name: str, reason: str) -> None:
        logger.warning(
            "Scope violation denied: tool=%s reason=%s", tool_name, reason
        )
        raise ScopeViolation(f"tool call '{tool_name}' out of patient scope: {reason}")
