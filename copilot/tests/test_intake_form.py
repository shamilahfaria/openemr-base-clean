"""intake_form — the second ingestable document type (MVP deliverable 1).

Mirrors the lab_pdf tests: a fake Anthropic client returns an intake draft via
the forced record_intake_fields tool; we assert strict validation, stamped
lineage (source_type=intake_form), the /documents surface, and that /ask
answers ground in intake facts alongside labs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.documents import routes as doc_routes
from app.documents.extractor import ExtractionError, VisionExtractor
from app.documents.ingest import InMemoryDocumentStore, attach_and_extract
from app.documents.schemas import (
    Confidence,
    IntakeExtractionDraft,
    SourceType,
    finalize_intake_extraction,
)
from app.main import create_app

INTAKE_INPUT = {
    "form_date": "2026-07-02",
    "fields": [
        {"field_name": "chief_complaint", "value": "shortness of breath",
         "section": "Reason for visit", "confidence": "high", "page": 1,
         "quote": "SOB x 3 days",
         "bbox": {"page": 1, "x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.15}},
        {"field_name": "allergy", "value": "penicillin — rash",
         "section": "Allergies", "confidence": "high", "page": 1,
         "quote": "PCN (rash)"},
        {"field_name": "smoking_status", "value": None,
         "section": "Social history", "confidence": "low", "page": 2,
         "quote": None},
    ],
}

LAB_INPUT = {
    "collection_date": "2026-07-01",
    "results": [
        {"test_name": "Hemoglobin A1c", "value": "6.7", "unit": "%",
         "confidence": "high", "abnormal_flag": "high", "page": 1,
         "quote": "A1c 6.7 %"}
    ],
}

_TOOL_INPUTS = {"record_lab_results": LAB_INPUT, "record_intake_fields": INTAKE_INPUT}


class FakeVisionClient:
    """Answers whichever extraction tool the request forces."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        tool_name = kwargs["tool_choice"]["name"]
        return SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name=tool_name, input=_TOOL_INPUTS[tool_name])
        ])


# --- schema / finalize ------------------------------------------------------


def test_finalize_stamps_intake_lineage():
    draft = IntakeExtractionDraft.model_validate(INTAKE_INPUT)
    extraction = finalize_intake_extraction(draft, document_id="docref-9", patient_id="pat-1")
    assert extraction.document_id == "docref-9"
    assert extraction.form_date == "2026-07-02"
    complaint = extraction.fields[0]
    assert complaint.citation.source_type is SourceType.INTAKE_FORM
    assert complaint.citation.source_id == "docref-9"
    assert complaint.citation.field_or_chunk_id == "chief_complaint"
    assert complaint.citation.quote_or_value == "SOB x 3 days"
    # Ungrounded field stays visible with a null value, never invented.
    smoking = extraction.fields[2]
    assert smoking.value is None
    assert smoking.confidence is Confidence.LOW


def test_unexpected_field_fails_validation():
    bad = {"fields": [{"field_name": "x", "confidence": "high", "invented": True}]}
    with pytest.raises(Exception):
        IntakeExtractionDraft.model_validate(bad)


# --- extractor --------------------------------------------------------------


@pytest.mark.anyio
async def test_extract_intake_forces_the_intake_tool():
    client = FakeVisionClient()
    extraction = await VisionExtractor(client).extract_intake(
        document_id="docref-9", patient_id="pat-1",
        media_type="application/pdf", data_b64="ZmFrZQ==",
    )
    assert client.calls[0]["tool_choice"] == {"type": "tool", "name": "record_intake_fields"}
    assert extraction.fields[1].value == "penicillin — rash"
    assert extraction.fields[1].citation.source_type is SourceType.INTAKE_FORM


@pytest.mark.anyio
async def test_attach_and_extract_dispatches_by_doc_type():
    store = InMemoryDocumentStore()
    extractor = VisionExtractor(FakeVisionClient())
    extraction = await attach_and_extract(
        patient_id="pat-1", filename="intake.pdf", media_type="application/pdf",
        data=b"%PDF-1.4 intake", doc_type="intake_form", store=store, extractor=extractor,
    )
    assert extraction.fields
    assert store.get_patient_extractions("pat-1") == [extraction]


@pytest.mark.anyio
async def test_attach_and_extract_rejects_unknown_doc_type():
    with pytest.raises(ExtractionError):
        await attach_and_extract(
            patient_id="pat-1", filename="x.pdf", media_type="application/pdf",
            data=b"%PDF", doc_type="xray",
            store=InMemoryDocumentStore(), extractor=VisionExtractor(FakeVisionClient()),
        )


# --- endpoint + graph -------------------------------------------------------


@pytest.fixture
def harness():
    app = create_app()
    store = InMemoryDocumentStore()
    app.dependency_overrides[doc_routes.get_document_extractor] = (
        lambda: VisionExtractor(FakeVisionClient())
    )
    app.dependency_overrides[doc_routes.get_document_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False), store


def _upload(client, doc_type: str, filename: str = "doc.pdf"):
    return client.post(
        "/documents",
        files={"file": (filename, f"%PDF-1.4 {filename}".encode(), "application/pdf")},
        data={"patient_id": "pat-1", "doc_type": doc_type},
        headers={"X-Clinician-Id": "nurse-maria"},
    )


def test_upload_intake_form_returns_cited_fields(harness):
    client, store = harness
    response = _upload(client, "intake_form")
    assert response.status_code == 200
    body = response.json()
    assert body["doc_type"] == "intake_form"
    assert body["result_count"] == 3
    assert body["results"] == []
    field = body["fields"][0]
    assert field["field_name"] == "chief_complaint"
    assert field["citation"]["source_type"] == "intake_form"
    assert field["citation"]["source_id"] == body["document_id"]
    assert len(store.get_patient_extractions("pat-1")) == 1


def test_ask_grounds_in_both_doc_types(harness):
    client, _ = harness
    assert _upload(client, "lab_pdf", "labs.pdf").status_code == 200
    assert _upload(client, "intake_form", "intake.pdf").status_code == 200

    response = client.post(
        "/ask",
        json={"patient_id": "pat-1", "question": "Summarize this patient's documents."},
        headers={"X-Clinician-Id": "nurse-maria"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False
    answer = body["answer"].lower()
    assert "a1c" in answer                    # lab fact
    assert "penicillin" in answer             # intake fact
    source_types = {c["source_type"] for c in body["citations"]}
    assert {"lab_pdf", "intake_form"} <= source_types
    # Null-valued intake fields are not presented as facts.
    assert "smoking_status" not in answer
