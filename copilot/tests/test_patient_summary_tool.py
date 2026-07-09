"""
TDD (Red) suite — build step 3b: the get_patient_summary tool.

Contract under test (app/tools/patient_summary.py):
  * aggregates Patient/{id} + active Conditions + recent Encounters
  * every record carries a source_id (FHIR resource id) — verifier needs it
  * encounters returned most-recent-first regardless of server order
  * inactive/resolved conditions never appear as active problems (AUDIT D5)
  * empty sections -> empty lists (boundary: empty patient record)
  * demographics failure -> tool fails; section failure -> partial summary
    with the failed section named in ``unavailable`` (Failure Modes table)
  * empty bearer token -> fail closed, no FHIR call is made
  * all reads scoped to the requested patient id

The FHIR transport is irrelevant here: a FakeFhirClient stands in for
app.fhir.client.FhirClient, so these tests pin tool behavior only.
"""
from __future__ import annotations

import pytest

from app.fhir.client import FhirAuthError, FhirNotFoundError, FhirUnavailableError
from app.tools.patient_summary import get_patient_summary

PATIENT_ID = "uuid-pat-1"
TOKEN = "test-bearer-token-123"


# --- FHIR resource builders ---------------------------------------------------

def patient_resource(**overrides) -> dict:
    resource = {
        "resourceType": "Patient",
        "id": PATIENT_ID,
        "name": [{"family": "Rivera", "given": ["Elena", "M"]}],
        "birthDate": "1948-03-02",
        "gender": "female",
    }
    resource.update(overrides)
    return resource


def condition_entry(cond_id: str, display: str, status: str, onset: str | None = None) -> dict:
    resource = {
        "resourceType": "Condition",
        "id": cond_id,
        "clinicalStatus": {"coding": [{"code": status}]},
        "code": {"text": display},
    }
    if onset:
        resource["onsetDateTime"] = onset
    return {"resource": resource}


def encounter_entry(enc_id: str, start: str, type_text: str = "Hospice inpatient") -> dict:
    return {
        "resource": {
            "resourceType": "Encounter",
            "id": enc_id,
            "period": {"start": start},
            "type": [{"text": type_text}],
            "reasonCode": [{"text": "Symptom management"}],
        }
    }


def bundle(entries: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "searchset", "entry": entries}


DEFAULT_CONDITIONS = bundle(
    [
        condition_entry("cond-1", "Metastatic pancreatic cancer", "active", "2025-11-01"),
        condition_entry("cond-2", "Chronic pain", "active"),
    ]
)
DEFAULT_ENCOUNTERS = bundle(
    [
        encounter_entry("enc-1", "2026-07-01T09:00:00Z"),
        encounter_entry("enc-2", "2026-06-15T14:30:00Z"),
    ]
)


class FakeFhirClient:
    """Stands in for FhirClient: canned response or exception per path prefix."""

    def __init__(self, responses: dict[str, object]):
        self._responses = responses
        self.calls: list[tuple[str, str, dict | None]] = []

    async def get(self, path: str, *, bearer_token: str, params: dict | None = None) -> dict:
        self.calls.append((path, bearer_token, params))
        for prefix, value in self._responses.items():
            if path.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected FHIR path requested: {path}")


def make_fake(
    patient: object = None,
    conditions: object = None,
    encounters: object = None,
) -> FakeFhirClient:
    return FakeFhirClient(
        {
            "Patient/": patient if patient is not None else patient_resource(),
            "Condition": conditions if conditions is not None else DEFAULT_CONDITIONS,
            "Encounter": encounters if encounters is not None else DEFAULT_ENCOUNTERS,
        }
    )


# --- happy path -----------------------------------------------------------------

class TestHappyPath:
    @pytest.mark.anyio
    async def test_demographics_populated_with_source_id_from_patient_resource(self):
        summary = await get_patient_summary(make_fake(), PATIENT_ID, TOKEN)
        assert summary.patient_id == PATIENT_ID
        assert summary.demographics.source_id == PATIENT_ID
        assert summary.demographics.family_name == "Rivera"
        assert summary.demographics.given_names == ["Elena", "M"]
        assert summary.demographics.birth_date == "1948-03-02"
        assert summary.demographics.gender == "female"

    @pytest.mark.anyio
    async def test_active_problems_included_with_source_ids(self):
        summary = await get_patient_summary(make_fake(), PATIENT_ID, TOKEN)
        assert [p.source_id for p in summary.active_problems] == ["cond-1", "cond-2"]
        assert summary.active_problems[0].display == "Metastatic pancreatic cancer"
        assert summary.active_problems[0].onset == "2025-11-01"
        assert all(p.clinical_status == "active" for p in summary.active_problems)

    @pytest.mark.anyio
    async def test_recent_encounters_included_with_source_ids(self):
        summary = await get_patient_summary(make_fake(), PATIENT_ID, TOKEN)
        assert [e.source_id for e in summary.recent_encounters] == ["enc-1", "enc-2"]
        assert summary.recent_encounters[0].start == "2026-07-01T09:00:00Z"
        assert summary.recent_encounters[0].type_display == "Hospice inpatient"

    @pytest.mark.anyio
    async def test_nothing_unavailable_when_all_sections_fetch(self):
        summary = await get_patient_summary(make_fake(), PATIENT_ID, TOKEN)
        assert summary.unavailable == []

    @pytest.mark.anyio
    async def test_encounters_sorted_most_recent_first_regardless_of_server_order(self):
        out_of_order = bundle(
            [
                encounter_entry("enc-old", "2026-06-01T08:00:00Z"),
                encounter_entry("enc-new", "2026-07-05T10:00:00Z"),
                encounter_entry("enc-mid", "2026-06-20T12:00:00Z"),
            ]
        )
        summary = await get_patient_summary(
            make_fake(encounters=out_of_order), PATIENT_ID, TOKEN
        )
        assert [e.source_id for e in summary.recent_encounters] == [
            "enc-new",
            "enc-mid",
            "enc-old",
        ]

    @pytest.mark.anyio
    async def test_all_reads_scoped_to_requested_patient(self):
        fake = make_fake()
        await get_patient_summary(fake, PATIENT_ID, TOKEN)
        for path, _token, params in fake.calls:
            if path.startswith("Patient/"):
                assert path == f"Patient/{PATIENT_ID}"
            else:
                assert (params or {}).get("patient") == PATIENT_ID

    @pytest.mark.anyio
    async def test_bearer_token_forwarded_on_every_read(self):
        fake = make_fake()
        await get_patient_summary(fake, PATIENT_ID, TOKEN)
        assert fake.calls, "expected FHIR reads"
        assert all(token == TOKEN for _path, token, _params in fake.calls)


# --- data-quality defenses (AUDIT D5) --------------------------------------------

class TestDataQualityDefenses:
    @pytest.mark.anyio
    async def test_inactive_and_resolved_conditions_are_excluded(self):
        mixed = bundle(
            [
                condition_entry("cond-active", "Metastatic pancreatic cancer", "active"),
                condition_entry("cond-resolved", "Pneumonia", "resolved"),
                condition_entry("cond-inactive", "Hypertension", "inactive"),
            ]
        )
        summary = await get_patient_summary(
            make_fake(conditions=mixed), PATIENT_ID, TOKEN
        )
        assert [p.source_id for p in summary.active_problems] == ["cond-active"]

    @pytest.mark.anyio
    async def test_patient_with_missing_name_and_birthdate_still_summarized(self):
        sparse = {"resourceType": "Patient", "id": PATIENT_ID}
        summary = await get_patient_summary(
            make_fake(patient=sparse), PATIENT_ID, TOKEN
        )
        assert summary.demographics.source_id == PATIENT_ID
        assert summary.demographics.family_name is None
        assert summary.demographics.given_names == []
        assert summary.demographics.birth_date is None


# --- boundaries -------------------------------------------------------------------

class TestBoundaries:
    @pytest.mark.anyio
    async def test_empty_condition_bundle_yields_empty_problem_list(self):
        summary = await get_patient_summary(
            make_fake(conditions=bundle([])), PATIENT_ID, TOKEN
        )
        assert summary.active_problems == []
        assert "problems" not in summary.unavailable  # empty is not a failure

    @pytest.mark.anyio
    async def test_empty_encounter_bundle_yields_empty_encounter_list(self):
        summary = await get_patient_summary(
            make_fake(encounters=bundle([])), PATIENT_ID, TOKEN
        )
        assert summary.recent_encounters == []
        assert "encounters" not in summary.unavailable

    @pytest.mark.anyio
    async def test_bundle_without_entry_key_treated_as_empty(self):
        # OpenEMR returns searchsets with no "entry" key when there are no hits.
        no_entry = {"resourceType": "Bundle", "type": "searchset"}
        summary = await get_patient_summary(
            make_fake(conditions=no_entry, encounters=no_entry), PATIENT_ID, TOKEN
        )
        assert summary.active_problems == []
        assert summary.recent_encounters == []


# --- failure semantics (fail closed / most complete verified summary) -------------

class TestFailureSemantics:
    @pytest.mark.anyio
    async def test_patient_not_found_propagates(self):
        with pytest.raises(FhirNotFoundError):
            await get_patient_summary(
                make_fake(patient=FhirNotFoundError()), PATIENT_ID, TOKEN
            )

    @pytest.mark.anyio
    async def test_auth_error_on_demographics_propagates(self):
        with pytest.raises(FhirAuthError):
            await get_patient_summary(
                make_fake(patient=FhirAuthError()), PATIENT_ID, TOKEN
            )

    @pytest.mark.anyio
    async def test_conditions_unavailable_yields_partial_summary(self):
        summary = await get_patient_summary(
            make_fake(conditions=FhirUnavailableError()), PATIENT_ID, TOKEN
        )
        assert summary.demographics.source_id == PATIENT_ID
        assert summary.active_problems == []
        assert "problems" in summary.unavailable
        # the healthy section is unaffected
        assert [e.source_id for e in summary.recent_encounters] == ["enc-1", "enc-2"]

    @pytest.mark.anyio
    async def test_encounters_unavailable_yields_partial_summary(self):
        summary = await get_patient_summary(
            make_fake(encounters=FhirUnavailableError()), PATIENT_ID, TOKEN
        )
        assert "encounters" in summary.unavailable
        assert summary.recent_encounters == []
        assert [p.source_id for p in summary.active_problems] == ["cond-1", "cond-2"]

    @pytest.mark.anyio
    async def test_empty_bearer_token_fails_closed_without_any_fhir_call(self):
        fake = make_fake()
        with pytest.raises((ValueError, FhirAuthError)):
            await get_patient_summary(fake, PATIENT_ID, "")
        assert fake.calls == []

    @pytest.mark.anyio
    async def test_whitespace_bearer_token_fails_closed_without_any_fhir_call(self):
        fake = make_fake()
        with pytest.raises((ValueError, FhirAuthError)):
            await get_patient_summary(fake, PATIENT_ID, "   ")
        assert fake.calls == []
