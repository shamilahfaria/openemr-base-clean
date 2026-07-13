"""Chart retrieval tools.

Shared contract (parametrized over all 8 tools): patient-scoped + bearer
forwarded, blank bearer fails closed with zero FHIR calls, empty searchset ->
empty list, FHIR errors propagate. Tool-specific contracts follow per class —
notably the D3 no-administration-timing rule (meds) and the A1
treatment-intervention-preference category rule (goals of care).
"""
from __future__ import annotations

import pytest

from app.fhir.client import FhirUnavailableError
from app.tools.chart import (
    GOALS_OF_CARE_CATEGORY,
    MedicationRecord,
    get_allergies,
    get_goals_of_care,
    get_labs,
    get_medications,
    get_problem_list,
    get_recent_encounters,
    get_vitals,
    search_notes,
)

PATIENT_ID = "uuid-pat-1"
TOKEN = "test-bearer-token-123"


def bundle(entries: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "searchset", "entry": entries}


EMPTY = bundle([])


class FakeFhirClient:
    """Canned response or exception per FHIR path prefix; records every call."""

    def __init__(self, responses: dict[str, object] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    async def get(self, path: str, *, bearer_token: str, params: dict | None = None) -> dict:
        self.calls.append((path, bearer_token, params))
        for prefix, value in self._responses.items():
            if path.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        return EMPTY


def run_tool(tool, client):
    """Uniform invocation across tools (search_notes needs a query)."""
    if tool is search_notes:
        return tool(client, PATIENT_ID, TOKEN, "pain")
    return tool(client, PATIENT_ID, TOKEN)


ALL_TOOLS = [
    get_medications,
    get_allergies,
    get_labs,
    get_vitals,
    get_problem_list,
    search_notes,
    get_recent_encounters,
    get_goals_of_care,
]


class TestSharedToolContract:
    @pytest.mark.anyio
    @pytest.mark.parametrize("tool", ALL_TOOLS)
    async def test_reads_scoped_to_patient_with_bearer_forwarded(self, tool):
        client = FakeFhirClient()
        await run_tool(tool, client)
        assert client.calls, "expected at least one FHIR read"
        for _path, token, params in client.calls:
            assert token == TOKEN
            assert (params or {}).get("patient") == PATIENT_ID

    @pytest.mark.anyio
    @pytest.mark.parametrize("tool", ALL_TOOLS)
    async def test_blank_bearer_fails_closed_without_any_fhir_call(self, tool):
        client = FakeFhirClient()
        with pytest.raises(ValueError):
            if tool is search_notes:
                await tool(client, PATIENT_ID, "  ", "pain")
            else:
                await tool(client, PATIENT_ID, "  ")
        assert client.calls == []

    @pytest.mark.anyio
    @pytest.mark.parametrize("tool", ALL_TOOLS)
    async def test_empty_searchset_yields_empty_list(self, tool):
        result = await run_tool(tool, FakeFhirClient())
        assert result == []

    @pytest.mark.anyio
    @pytest.mark.parametrize("tool", ALL_TOOLS)
    async def test_fhir_errors_propagate(self, tool):
        client = FakeFhirClient(
            {
                "MedicationRequest": FhirUnavailableError(),
                "AllergyIntolerance": FhirUnavailableError(),
                "Observation": FhirUnavailableError(),
                "Condition": FhirUnavailableError(),
                "Encounter": FhirUnavailableError(),
                "DocumentReference": FhirUnavailableError(),
            }
        )
        with pytest.raises(FhirUnavailableError):
            await run_tool(tool, client)


class TestMedications:
    RESOURCE = {
        "resourceType": "MedicationRequest",
        "id": "med-1",
        "status": "active",
        "medicationCodeableConcept": {"text": "Morphine sulfate"},
        "dosageInstruction": [
            {
                "text": "0.25 mg IV q4h PRN pain",
                "asNeededBoolean": True,
                "timing": {"code": {"text": "Q4H"}},
                "route": {"text": "IV"},
                "doseAndRate": [{"doseQuantity": {"value": 0.25, "unit": "mg"}}],
            }
        ],
    }

    def make_client(self, resource=None):
        return FakeFhirClient(
            {"MedicationRequest": bundle([{"resource": resource or self.RESOURCE}])}
        )

    @pytest.mark.anyio
    async def test_parses_order_fields(self):
        (record,) = await get_medications(self.make_client(), PATIENT_ID, TOKEN)
        assert record.source_id == "med-1"
        assert record.name == "Morphine sulfate"
        assert record.dose == "0.25 mg"
        assert record.route == "IV"
        assert record.sig == "0.25 mg IV q4h PRN pain"
        assert record.status == "active"

    @pytest.mark.anyio
    async def test_prn_flag_and_interval_captured(self):
        (record,) = await get_medications(self.make_client(), PATIENT_ID, TOKEN)
        assert record.is_prn is True
        assert record.prn_interval == "Q4H"

    @pytest.mark.anyio
    async def test_non_prn_order(self):
        scheduled = {
            "resourceType": "MedicationRequest",
            "id": "med-2",
            "status": "active",
            "medicationCodeableConcept": {"text": "Senna"},
            "dosageInstruction": [{"text": "8.6 mg PO daily", "asNeededBoolean": False}],
        }
        (record,) = await get_medications(self.make_client(scheduled), PATIENT_ID, TOKEN)
        assert record.is_prn is False
        assert record.prn_interval is None

    @pytest.mark.anyio
    async def test_missing_dosage_instruction_tolerated(self):
        sparse = {
            "resourceType": "MedicationRequest",
            "id": "med-3",
            "medicationCodeableConcept": {"text": "Lorazepam"},
        }
        (record,) = await get_medications(self.make_client(sparse), PATIENT_ID, TOKEN)
        assert record.name == "Lorazepam"
        assert record.is_prn is False
        assert record.dose is None

    def test_record_carries_no_administration_timing_field(self):
        # AUDIT D3: administration timing does not exist in OpenEMR's data
        # model. The contract must not even be able to express it.
        assert not any("administer" in name for name in MedicationRecord.model_fields)


class TestAllergies:
    RESOURCE = {
        "resourceType": "AllergyIntolerance",
        "id": "alg-1",
        "code": {"text": "Penicillin"},
        "criticality": "high",
        "reaction": [
            {"manifestation": [{"text": "Anaphylaxis"}, {"text": "Hives"}]},
        ],
    }

    @pytest.mark.anyio
    async def test_parses_substance_criticality_and_reactions(self):
        client = FakeFhirClient({"AllergyIntolerance": bundle([{"resource": self.RESOURCE}])})
        (record,) = await get_allergies(client, PATIENT_ID, TOKEN)
        assert record.source_id == "alg-1"
        assert record.substance == "Penicillin"
        assert record.criticality == "high"
        assert record.reactions == ["Anaphylaxis", "Hives"]

    @pytest.mark.anyio
    async def test_allergy_without_reactions_yields_empty_reaction_list(self):
        sparse = {"resourceType": "AllergyIntolerance", "id": "alg-2", "code": {"text": "Latex"}}
        client = FakeFhirClient({"AllergyIntolerance": bundle([{"resource": sparse}])})
        (record,) = await get_allergies(client, PATIENT_ID, TOKEN)
        assert record.reactions == []
        assert record.criticality is None


def observation(obs_id: str, display: str, value: float, unit: str, effective: str) -> dict:
    return {
        "resource": {
            "resourceType": "Observation",
            "id": obs_id,
            "code": {"text": display},
            "valueQuantity": {"value": value, "unit": unit},
            "effectiveDateTime": effective,
        }
    }


class TestLabsAndVitals:
    @pytest.mark.anyio
    async def test_labs_query_uses_laboratory_category(self):
        client = FakeFhirClient()
        await get_labs(client, PATIENT_ID, TOKEN)
        (_path, _token, params) = client.calls[0]
        assert params.get("category") == "laboratory"

    @pytest.mark.anyio
    async def test_vitals_query_uses_vital_signs_category(self):
        client = FakeFhirClient()
        await get_vitals(client, PATIENT_ID, TOKEN)
        (_path, _token, params) = client.calls[0]
        assert params.get("category") == "vital-signs"

    @pytest.mark.anyio
    async def test_parses_values_and_sorts_most_recent_first(self):
        out_of_order = bundle(
            [
                observation("lab-old", "Creatinine", 1.1, "mg/dL", "2026-06-01"),
                observation("lab-new", "Creatinine", 1.8, "mg/dL", "2026-07-06"),
            ]
        )
        client = FakeFhirClient({"Observation": out_of_order})
        records = await get_labs(client, PATIENT_ID, TOKEN)
        assert [r.source_id for r in records] == ["lab-new", "lab-old"]
        assert records[0].display == "Creatinine"
        assert records[0].value == "1.8"
        assert records[0].unit == "mg/dL"
        assert records[0].effective == "2026-07-06"


class TestObservationCodingFallback:
    """Real OpenEMR FHIR omits code.text and value.text, filling only
    coding[].display — the parser must read the coding so labs/vitals carry a
    citable name and value instead of degrading the turn."""

    @pytest.mark.anyio
    async def test_display_falls_back_to_coding_when_text_absent(self):
        resource = {
            "resource": {
                "resourceType": "Observation",
                "id": "lab-c",
                "code": {"coding": [{"code": "2160-0", "display": "Creatinine"}]},
                "valueQuantity": {"value": 1.8, "unit": "mg/dL"},
                "effectiveDateTime": "2026-07-06",
            }
        }
        client = FakeFhirClient({"Observation": bundle([resource])})
        (record,) = await get_labs(client, PATIENT_ID, TOKEN)
        assert record.display == "Creatinine"
        assert record.value == "1.8"

    @pytest.mark.anyio
    async def test_qualitative_value_read_from_codeable_concept(self):
        resource = {
            "resource": {
                "resourceType": "Observation",
                "id": "lab-q",
                "code": {"coding": [{"display": "MRSA screen"}]},
                "valueCodeableConcept": {"coding": [{"display": "Positive"}]},
                "effectiveDateTime": "2026-07-06",
            }
        }
        client = FakeFhirClient({"Observation": bundle([resource])})
        (record,) = await get_labs(client, PATIENT_ID, TOKEN)
        assert record.display == "MRSA screen"
        assert record.value == "Positive"
        assert record.unit is None


class TestProblemList:
    @pytest.mark.anyio
    async def test_display_falls_back_to_coding_when_text_absent(self):
        coded = bundle(
            [
                {
                    "resource": {
                        "resourceType": "Condition",
                        "id": "cond-c",
                        "clinicalStatus": {"coding": [{"code": "active"}]},
                        "code": {"coding": [{"code": "363418001", "display": "Metastatic pancreatic cancer"}]},
                    }
                }
            ]
        )
        client = FakeFhirClient({"Condition": coded})
        (record,) = await get_problem_list(client, PATIENT_ID, TOKEN)
        assert record.display == "Metastatic pancreatic cancer"

    @pytest.mark.anyio
    async def test_includes_active_and_historical_problems(self):
        def condition(cond_id, display, status):
            return {
                "resource": {
                    "resourceType": "Condition",
                    "id": cond_id,
                    "clinicalStatus": {"coding": [{"code": status}]},
                    "code": {"text": display},
                }
            }

        mixed = bundle(
            [
                condition("cond-1", "Metastatic pancreatic cancer", "active"),
                condition("cond-2", "Pneumonia", "resolved"),
            ]
        )
        client = FakeFhirClient({"Condition": mixed})
        records = await get_problem_list(client, PATIENT_ID, TOKEN)
        assert [(r.source_id, r.clinical_status) for r in records] == [
            ("cond-1", "active"),
            ("cond-2", "resolved"),
        ]


class TestSearchNotes:
    def make_client(self):
        def note(note_id, description, date):
            return {
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": note_id,
                    "date": date,
                    "description": description,
                }
            }

        return FakeFhirClient(
            {
                "DocumentReference": bundle(
                    [
                        note("note-1", "Overnight PAIN escalation, morphine effective", "2026-07-05"),
                        note("note-2", "Family meeting re goals of care", "2026-07-04"),
                    ]
                )
            }
        )

    @pytest.mark.anyio
    async def test_returns_only_notes_matching_query_case_insensitively(self):
        records = await search_notes(self.make_client(), PATIENT_ID, TOKEN, "pain")
        assert [r.source_id for r in records] == ["note-1"]
        assert records[0].date == "2026-07-05"

    @pytest.mark.anyio
    async def test_no_matches_yields_empty_list(self):
        records = await search_notes(self.make_client(), PATIENT_ID, TOKEN, "dialysis")
        assert records == []


class TestRecentEncounters:
    @pytest.mark.anyio
    async def test_sorted_most_recent_first(self):
        def encounter(enc_id, start):
            return {
                "resource": {
                    "resourceType": "Encounter",
                    "id": enc_id,
                    "period": {"start": start},
                }
            }

        client = FakeFhirClient(
            {
                "Encounter": bundle(
                    [
                        encounter("enc-old", "2026-06-01T08:00:00Z"),
                        encounter("enc-new", "2026-07-05T10:00:00Z"),
                    ]
                )
            }
        )
        records = await get_recent_encounters(client, PATIENT_ID, TOKEN)
        assert [r.source_id for r in records] == ["enc-new", "enc-old"]


class TestGoalsOfCare:
    RESOURCE = {
        "resourceType": "Observation",
        "id": "goc-1",
        "code": {"coding": [{"code": "81329-5"}], "text": "Thoughts on resuscitation (CPR)"},
        "valueCodeableConcept": {"text": "No CPR (Do Not Attempt Resuscitation)"},
        "effectiveDateTime": "2026-06-01",
    }

    @pytest.mark.anyio
    async def test_queries_treatment_intervention_preference_category(self):
        # AUDIT A1: code status lives in Observation with this category —
        # Goal/Consent return wrong data or 404.
        client = FakeFhirClient()
        await get_goals_of_care(client, PATIENT_ID, TOKEN)
        (_path, _token, params) = client.calls[0]
        assert params.get("category") == GOALS_OF_CARE_CATEGORY

    @pytest.mark.anyio
    async def test_parses_code_question_and_answer(self):
        client = FakeFhirClient({"Observation": bundle([{"resource": self.RESOURCE}])})
        (record,) = await get_goals_of_care(client, PATIENT_ID, TOKEN)
        assert record.source_id == "goc-1"
        assert record.code == "81329-5"
        assert record.question == "Thoughts on resuscitation (CPR)"
        assert record.answer == "No CPR (Do Not Attempt Resuscitation)"
        assert record.effective == "2026-06-01"

    @pytest.mark.anyio
    async def test_missing_value_yields_none_answer_not_a_guess(self):
        sparse = {
            "resourceType": "Observation",
            "id": "goc-2",
            "code": {"coding": [{"code": "75773-2"}], "text": "Goals for medical treatment"},
        }
        client = FakeFhirClient({"Observation": bundle([{"resource": sparse}])})
        (record,) = await get_goals_of_care(client, PATIENT_ID, TOKEN)
        assert record.answer is None


class TestGoalsOfCareCodingFallback:
    @pytest.mark.anyio
    async def test_reads_value_and_question_from_coding_when_text_absent(self):
        # Real OpenEMR FHIR puts the value under coding[].display, not .text.
        resource = {
            "resourceType": "Observation",
            "id": "goc-9",
            "code": {"coding": [{"code": "75773-2", "display": "Goals for medical treatment"}]},
            "valueCodeableConcept": {
                "coding": [{"code": "385644000", "display": "Prefers limited resuscitation"}]
            },
        }
        client = FakeFhirClient({"Observation": bundle([{"resource": resource}])})
        (record,) = await get_goals_of_care(client, PATIENT_ID, TOKEN)
        assert record.source_id == "goc-9"
        assert record.question == "Goals for medical treatment"
        assert record.answer == "Prefers limited resuscitation"
