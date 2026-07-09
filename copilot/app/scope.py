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

# Argument names that reference a patient. Any of these naming a different
# patient than the active chart is a cross-patient access attempt.
PATIENT_REF_KEYS = ("patient_id", "patient", "pid", "subject")


class ScopeViolation(Exception):
    """A tool call attempted to reach outside the active patient's chart."""


class PatientScopeGuard:
    """Hard-scopes every tool call to the active patient (AUDIT S1)."""

    def __init__(self, active_patient_id: str):
        raise NotImplementedError

    def validate_tool_call(self, tool_name: str, arguments: dict) -> None:
        """Raise ``ScopeViolation`` unless ``arguments`` is scoped to the active patient."""
        raise NotImplementedError
