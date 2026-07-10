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
from .patient_summary import (
    EncounterRecord,
    ProblemRecord,
    _bundle_resources,
    _clinical_status,
    _parse_recent_encounters,
)

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


def _require_bearer(bearer_token: str) -> None:
    if not bearer_token.strip():
        # Fail closed before any network call.
        raise ValueError("bearer token is required")


async def _search(
    client: FhirClient,
    resource: str,
    patient_id: str,
    bearer_token: str,
    extra_params: dict | None = None,
) -> list[dict]:
    params = {"patient": patient_id, **(extra_params or {})}
    bundle = await client.get(resource, bearer_token=bearer_token, params=params)
    return _bundle_resources(bundle)


def _dose_text(dosage: dict) -> str | None:
    for dose_and_rate in dosage.get("doseAndRate") or []:
        quantity = dose_and_rate.get("doseQuantity") or {}
        if "value" in quantity:
            unit = quantity.get("unit")
            return f"{quantity['value']} {unit}" if unit else str(quantity["value"])
    return None


async def get_medications(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[MedicationRecord]:
    _require_bearer(bearer_token)
    records = []
    for resource in await _search(client, "MedicationRequest", patient_id, bearer_token):
        dosages = resource.get("dosageInstruction") or []
        dosage = dosages[0] if dosages else {}
        is_prn = bool(dosage.get("asNeededBoolean"))
        timing_code = ((dosage.get("timing") or {}).get("code") or {}).get("text")
        records.append(
            MedicationRecord(
                source_id=resource["id"],
                name=(resource.get("medicationCodeableConcept") or {}).get("text", ""),
                dose=_dose_text(dosage),
                route=(dosage.get("route") or {}).get("text"),
                sig=dosage.get("text"),
                is_prn=is_prn,
                prn_interval=timing_code if is_prn else None,
                status=resource.get("status"),
            )
        )
    return records


async def get_allergies(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[AllergyRecord]:
    _require_bearer(bearer_token)
    records = []
    for resource in await _search(client, "AllergyIntolerance", patient_id, bearer_token):
        reactions = [
            manifestation["text"]
            for reaction in resource.get("reaction") or []
            for manifestation in reaction.get("manifestation") or []
            if "text" in manifestation
        ]
        records.append(
            AllergyRecord(
                source_id=resource["id"],
                substance=(resource.get("code") or {}).get("text", ""),
                criticality=resource.get("criticality"),
                reactions=reactions,
            )
        )
    return records


def _parse_observations(resources: list[dict]) -> list[ObservationRecord]:
    records = []
    for resource in resources:
        quantity = resource.get("valueQuantity") or {}
        records.append(
            ObservationRecord(
                source_id=resource["id"],
                display=(resource.get("code") or {}).get("text", ""),
                value=str(quantity["value"]) if "value" in quantity else None,
                unit=quantity.get("unit"),
                effective=resource.get("effectiveDateTime"),
            )
        )
    records.sort(key=lambda r: r.effective or "", reverse=True)
    return records


async def get_labs(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[ObservationRecord]:
    _require_bearer(bearer_token)
    resources = await _search(
        client, "Observation", patient_id, bearer_token, {"category": "laboratory"}
    )
    return _parse_observations(resources)


async def get_vitals(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[ObservationRecord]:
    _require_bearer(bearer_token)
    resources = await _search(
        client, "Observation", patient_id, bearer_token, {"category": "vital-signs"}
    )
    return _parse_observations(resources)


async def get_problem_list(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[ProblemRecord]:
    _require_bearer(bearer_token)
    return [
        ProblemRecord(
            source_id=resource["id"],
            display=(resource.get("code") or {}).get("text", ""),
            clinical_status=_clinical_status(resource),
            onset=resource.get("onsetDateTime"),
        )
        for resource in await _search(client, "Condition", patient_id, bearer_token)
    ]


async def search_notes(
    client: FhirClient, patient_id: str, bearer_token: str, query: str
) -> list[NoteRecord]:
    _require_bearer(bearer_token)
    needle = query.lower()
    return [
        NoteRecord(
            source_id=resource["id"],
            date=resource.get("date"),
            description=resource.get("description", ""),
        )
        for resource in await _search(client, "DocumentReference", patient_id, bearer_token)
        if needle in resource.get("description", "").lower()
    ]


async def get_recent_encounters(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[EncounterRecord]:
    _require_bearer(bearer_token)
    bundle = await client.get(
        "Encounter", bearer_token=bearer_token, params={"patient": patient_id}
    )
    return _parse_recent_encounters(bundle)


async def get_goals_of_care(
    client: FhirClient, patient_id: str, bearer_token: str
) -> list[GoalsOfCareRecord]:
    _require_bearer(bearer_token)
    resources = await _search(
        client, "Observation", patient_id, bearer_token,
        {"category": GOALS_OF_CARE_CATEGORY},
    )
    def codeable_text(concept: dict | None) -> str | None:
        concept = concept or {}
        if concept.get("text"):
            return concept["text"]
        codings = concept.get("coding") or []
        return codings[0].get("display") if codings else None

    records = []
    for resource in resources:
        code = resource.get("code") or {}
        codings = code.get("coding") or []
        records.append(
            GoalsOfCareRecord(
                source_id=resource.get("id", ""),
                code=codings[0].get("code", "") if codings else "",
                question=codeable_text(code) or "",
                answer=codeable_text(resource.get("valueCodeableConcept")),
                effective=resource.get("effectiveDateTime"),
            )
        )
    return records
