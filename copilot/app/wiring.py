"""Production wiring — STUB (no implementation yet).

Builds the real dependency graph behind chat.py's provider seams:
settings -> FhirClient -> tool registry -> Orchestrator (Anthropic client via
``build_anthropic_client`` so the SDK import stays out of tests) -> Verifier
(rules loaded from the versioned JSON file) -> AuditTrail -> fallback provider.

Providers are process singletons — the audit trail and session store must
persist across requests. ``reset()`` clears them (tests only).
"""
from __future__ import annotations

import json
from typing import Any

from .audit import AuditTrail
from .chat import FallbackFn
from .config import load_settings
from .fhir.client import FhirClient
from .orchestrator import Orchestrator
from .sessions import SessionStore
from .tools import chart
from .tools.patient_summary import get_patient_summary
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
    with open(path, encoding="utf-8") as handle:   # missing -> OSError
        data = json.load(handle)                    # malformed -> ValueError
    return ClinicalRuleSet.model_validate(data)     # invalid shape -> ValueError


def build_tool_registry(client: FhirClient) -> dict:
    """Adapters from tool name -> async (arguments, bearer_token) callables."""

    def simple(tool_fn):
        async def adapter(arguments: dict, bearer_token: str) -> list:
            return await tool_fn(client, arguments["patient_id"], bearer_token)

        return adapter

    async def summary_adapter(arguments: dict, bearer_token: str) -> list:
        summary = await get_patient_summary(client, arguments["patient_id"], bearer_token)
        # Flatten so every retrieved record carries a source_id for the verifier.
        return [summary.demographics, *summary.active_problems, *summary.recent_encounters]

    async def notes_adapter(arguments: dict, bearer_token: str) -> list:
        return await chart.search_notes(
            client, arguments["patient_id"], bearer_token, arguments.get("query", "")
        )

    return {
        "get_patient_summary": summary_adapter,
        "get_recent_encounters": simple(chart.get_recent_encounters),
        "search_notes": notes_adapter,
        "get_medications": simple(chart.get_medications),
        "get_allergies": simple(chart.get_allergies),
        "get_labs": simple(chart.get_labs),
        "get_vitals": simple(chart.get_vitals),
        "get_problem_list": simple(chart.get_problem_list),
        "get_goals_of_care": simple(chart.get_goals_of_care),
    }


def build_fallback_provider(client: FhirClient) -> FallbackFn:
    """Recent-visit-history fallback; never returns a blank answer."""

    async def fallback(patient_id: str, bearer_token: str) -> str:
        encounters = await chart.get_recent_encounters(client, patient_id, bearer_token)
        if not encounters:
            return "No recent visit history is available for this patient."
        lines = ["Recent visit history:"]
        for encounter in encounters[:5]:
            line = f"- {encounter.start or 'unknown date'} — {encounter.type_display or 'visit'}"
            if encounter.reason:
                line += f" ({encounter.reason})"
            lines.append(line)
        return "\n".join(lines)

    return fallback


def build_anthropic_client(api_key: str) -> Any:
    """Construct the AsyncAnthropic client (isolated so tests can fake it)."""
    from anthropic import AsyncAnthropic  # imported lazily; tests never need it

    return AsyncAnthropic(api_key=api_key)


_cache: dict[str, Any] = {}


def _fhir_client() -> FhirClient:
    if "fhir_client" not in _cache:
        _cache["fhir_client"] = FhirClient(load_settings().openemr_fhir_base_url)
    return _cache["fhir_client"]


def get_orchestrator() -> Orchestrator:
    if "orchestrator" not in _cache:
        settings = load_settings()
        _cache["orchestrator"] = Orchestrator(
            build_anthropic_client(settings.anthropic_api_key),
            build_tool_registry(_fhir_client()),
            SessionStore(),
            model=settings.anthropic_model,
        )
    return _cache["orchestrator"]


def get_verifier() -> Verifier:
    if "verifier" not in _cache:
        _cache["verifier"] = Verifier(
            load_clinical_rules(load_settings().clinical_rules_path)
        )
    return _cache["verifier"]


def get_audit_trail() -> AuditTrail:
    if "audit_trail" not in _cache:
        _cache["audit_trail"] = AuditTrail()
    return _cache["audit_trail"]


def get_fallback_provider() -> FallbackFn:
    if "fallback" not in _cache:
        _cache["fallback"] = build_fallback_provider(_fhir_client())
    return _cache["fallback"]


def reset() -> None:
    """Clear cached singletons (tests only)."""
    _cache.clear()
