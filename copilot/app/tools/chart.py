"""Chart retrieval tools — STUBS (contracts only).

Read-only FHIR tools per ARCHITECTURE.md's tool table. Shared rules:
  * every record carries a ``source_id`` (FHIR resource id) for attribution
  * all reads scoped to the requested patient
  * blank bearer token fails closed BEFORE any network call
  * FHIR errors propagate — the orchestrator owns skip/retry/fallback
  * empty searchsets are empty lists, never errors

Tool-specific constraints from the audit:
  * get_medications: orders only — dose/route/PRN flag/interval/sig. The data
    model has NO administration timing (AUDIT D3); the record type must not
    even carry such a field.
  * get_goals_of_care: FHIR ``Observation?category=treatment-intervention-
    preference`` — never Goal/Consent (AUDIT A1).
  * get_problem_list: active AND historical problems (summary shows active only).
"""
from __future__ import annotations

from pydantic import BaseModel

from ..fhir.client import FhirClient
from .patient_summary import EncounterRecord, ProblemRecord

GOALS_OF_CARE_CATEGORY = "treatment-intervention-preference"


class MedicationRecord(BaseModel):
    source_id: str
    name: str
    dose: str | None            # e.g. "0.25 mg" — as ordered
    route: str | None
    sig: str | None             # free-text dosage instruction
    is_prn: bool
    prn_interval: str | None    # e.g. "Q4H" — as ordered; never administration timing
    status: str | None


class AllergyRecord(BaseModel):
    source_id: str
    substance: str
    criticality: str | None
    reactions: list[str]


class ObservationRecord(BaseModel):
    source_id: str
    display: str
    value: str | None
    unit: str | None
    effective: str | None


class NoteRecord(BaseModel):
    source_id: str
    date: str | None
    description: str


class GoalsOfCareRecord(BaseModel):
    source_id: str
    code: str                   # LOINC, e.g. "81329-5"
    question: str               # e.g. "Thoughts on resuscitation (CPR)"
    answer: str | None          # e.g. "No CPR (Do Not Attempt Resuscitation)"
    effective: str | None


async def get_medications(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[MedicationRecord]:
    raise NotImplementedError


async def get_allergies(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[AllergyRecord]:
    raise NotImplementedError


async def get_labs(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[ObservationRecord]:
    raise NotImplementedError


async def get_vitals(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[ObservationRecord]:
    raise NotImplementedError


async def get_problem_list(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[ProblemRecord]:
    raise NotImplementedError


async def search_notes(
    client: FhirClient, patient_id: str, bearer_token: str, query: str
) -> list[NoteRecord]:
    raise NotImplementedError


async def get_recent_encounters(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[EncounterRecord]:
    raise NotImplementedError


async def get_goals_of_care(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[GoalsOfCareRecord]:
    raise NotImplementedError
