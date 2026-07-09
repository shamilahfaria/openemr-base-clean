"""get_patient_summary — cheap patient orientation tool. STUB (contracts only).

ARCHITECTURE.md tool table: "demographics, active problems, recent context."
Backed by three FHIR reads: ``Patient/{id}``, ``Condition`` (active), and
``Encounter`` (recent). Every record carries a ``source_id`` (the FHIR resource
id) — required downstream by the verifier for source attribution.

Failure semantics (ARCHITECTURE.md Failure Modes):
  * Demographics unavailable -> the tool fails (nothing to orient on).
  * Problems/encounters unavailable -> return the most complete verified
    summary; name the failed section in ``unavailable``.
  * Never guess: inactive conditions are never reported as active problems,
    even if the server returns them (AUDIT.md D5).
  * Empty bearer token -> fail closed BEFORE any network call.
"""
from __future__ import annotations

from pydantic import BaseModel

from ..fhir.client import FhirClient, FhirError


class Demographics(BaseModel):
    source_id: str              # FHIR Patient.id — attribution
    family_name: str | None
    given_names: list[str]
    birth_date: str | None      # ISO date as recorded; may be absent (AUDIT D5)
    gender: str | None


class ProblemRecord(BaseModel):
    source_id: str              # FHIR Condition.id
    display: str
    clinical_status: str        # always "active" in summary output
    onset: str | None


class EncounterRecord(BaseModel):
    source_id: str              # FHIR Encounter.id
    start: str | None           # ISO datetime of Encounter.period.start
    type_display: str | None
    reason: str | None


class PatientSummary(BaseModel):
    patient_id: str
    demographics: Demographics
    active_problems: list[ProblemRecord]
    recent_encounters: list[EncounterRecord]   # most recent first
    unavailable: list[str]      # sections that could not be fetched ("problems", "encounters")


def _bundle_resources(bundle: dict) -> list[dict]:
    """Resources from a FHIR searchset Bundle; no ``entry`` key means no hits."""
    return [entry["resource"] for entry in bundle.get("entry", []) if "resource" in entry]


def _parse_demographics(patient: dict) -> Demographics:
    names = patient.get("name") or []
    primary = names[0] if names else {}
    return Demographics(
        source_id=patient["id"],
        family_name=primary.get("family"),
        given_names=list(primary.get("given") or []),
        birth_date=patient.get("birthDate"),
        gender=patient.get("gender"),
    )


def _clinical_status(condition: dict) -> str:
    codings = (condition.get("clinicalStatus") or {}).get("coding") or []
    return codings[0].get("code", "") if codings else ""


def _parse_active_problems(bundle: dict) -> list[ProblemRecord]:
    problems = []
    for resource in _bundle_resources(bundle):
        status = _clinical_status(resource)
        if status != "active":
            # Never report a non-active condition as an active problem (AUDIT D5).
            continue
        problems.append(
            ProblemRecord(
                source_id=resource["id"],
                display=(resource.get("code") or {}).get("text", ""),
                clinical_status=status,
                onset=resource.get("onsetDateTime"),
            )
        )
    return problems


def _parse_recent_encounters(bundle: dict) -> list[EncounterRecord]:
    encounters = []
    for resource in _bundle_resources(bundle):
        types = resource.get("type") or []
        reasons = resource.get("reasonCode") or []
        encounters.append(
            EncounterRecord(
                source_id=resource["id"],
                start=(resource.get("period") or {}).get("start"),
                type_display=types[0].get("text") if types else None,
                reason=reasons[0].get("text") if reasons else None,
            )
        )
    encounters.sort(key=lambda e: e.start or "", reverse=True)
    return encounters


async def get_patient_summary(
    client: FhirClient,
    patient_id: str,
    bearer_token: str,
) -> PatientSummary:
    """Fetch and assemble the patient-orientation summary (see module docstring)."""
    if not bearer_token.strip():
        # Fail closed before any network call.
        raise ValueError("bearer token is required")

    # Demographics are mandatory: without them there is nothing to orient on,
    # so any failure here propagates (fail closed).
    patient = await client.get(f"Patient/{patient_id}", bearer_token=bearer_token)
    demographics = _parse_demographics(patient)

    unavailable: list[str] = []
    scoped = {"patient": patient_id}

    try:
        conditions = await client.get("Condition", bearer_token=bearer_token, params=scoped)
        active_problems = _parse_active_problems(conditions)
    except FhirError:
        # Most complete verified summary: name the failed section, never guess.
        active_problems = []
        unavailable.append("problems")

    try:
        encounters = await client.get("Encounter", bearer_token=bearer_token, params=scoped)
        recent_encounters = _parse_recent_encounters(encounters)
    except FhirError:
        recent_encounters = []
        unavailable.append("encounters")

    return PatientSummary(
        patient_id=patient_id,
        demographics=demographics,
        active_problems=active_problems,
        recent_encounters=recent_encounters,
        unavailable=unavailable,
    )
