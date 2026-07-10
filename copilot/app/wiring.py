"""Production wiring — STUB (no implementation yet).

Builds the real dependency graph behind chat.py's provider seams:
settings -> FhirClient -> tool registry -> Orchestrator (Anthropic client via
``build_anthropic_client`` so the SDK import stays out of tests) -> Verifier
(rules loaded from the versioned JSON file) -> AuditTrail -> fallback provider.

Providers are process singletons — the audit trail and session store must
persist across requests. ``reset()`` clears them (tests only).
"""
from __future__ import annotations

from typing import Any

from .audit import AuditTrail
from .chat import FallbackFn
from .fhir.client import FhirClient
from .orchestrator import Orchestrator
from .verifier import ClinicalRuleSet, Verifier

# The complete tool surface (ARCHITECTURE.md tool table).
TOOL_NAMES = (
    "get_patient_summary",
    "get_recent_encounters",
    "search_notes",
    "get_medications",
    "get_allergies",
    "get_labs",
    "get_vitals",
    "get_problem_list",
    "get_goals_of_care",
)


def load_clinical_rules(path: str) -> ClinicalRuleSet:
    """Parse the versioned rules JSON; missing/malformed file -> error."""
    raise NotImplementedError


def build_tool_registry(client: FhirClient) -> dict:
    """Adapters from tool name -> async (arguments, bearer_token) callables."""
    raise NotImplementedError


def build_fallback_provider(client: FhirClient) -> FallbackFn:
    """Recent-visit-history fallback; never returns a blank answer."""
    raise NotImplementedError


def build_anthropic_client(api_key: str) -> Any:
    """Construct the AsyncAnthropic client (isolated so tests can fake it)."""
    raise NotImplementedError


def get_orchestrator() -> Orchestrator:
    raise NotImplementedError


def get_verifier() -> Verifier:
    raise NotImplementedError


def get_audit_trail() -> AuditTrail:
    raise NotImplementedError


def get_fallback_provider() -> FallbackFn:
    raise NotImplementedError


def reset() -> None:
    """Clear cached singletons (tests only)."""
    raise NotImplementedError
