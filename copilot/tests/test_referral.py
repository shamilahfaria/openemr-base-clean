"""referral — the third ingestable document type (stretch deliverable).

Referral faxes/letters share the sectioned-field shape with intake forms but
carry their own SourceType, so every citation names the true document class.
Mirrors the intake tests: fake Anthropic client answers the forced
record_referral_fields tool; we assert strict validation, stamped lineage,
the /documents surface, and that /ask grounds in referral facts.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.documents import routes as doc_routes
from app.documents.extractor import VisionExtractor
from app.documents.ingest import InMemoryDocumentStore
from app.documents.schemas import (
    ReferralExtractionDraft,
    SourceType,
    finalize_referral_extraction,
)
from app.main import create_app

REFERRAL_INPUT = {
    "referral_date": "2026-07-05",
    "fields": [
        {"field_name": "referring_provider", "value": "Dr. Amara Okafor, MD — Lakeside Internal Medicine",
         "section": "Referring provider", "confidence": "high", "page": 1,
         "quote": "From: Amara Okafor, MD, Lakeside Internal Medicine"},
        {"field_name": "reason_for_referral", "value": "hospice evaluation for advanced heart failure",
         "section": "Reason for referral", "confidence": "high", "page": 1,
         "quote": "Requesting hospice evaluation — advanced HF"},
        {"field_name": "requested_service", "value": None,
         "section": "Service requested", "confidence": "low", "page": 2,
         "quote": None},
    ],
}


def _fake_extractor() -> VisionExtractor:
    async def create(**kwargs):
        tool_name = kwargs["tool_choice"]["name"]
        return SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name=tool_name, input=REFERRAL_INPUT)
        ])
    return VisionExtractor(SimpleNamespace(messages=SimpleNamespace(create=create)))


def test_finalize_stamps_referral_lineage():
    draft = ReferralExtractionDraft.model_validate(REFERRAL_INPUT)
    extraction = finalize_referral_extraction(draft, document_id="docref-7", patient_id="pat-1")
    assert extraction.referral_date == "2026-07-05"
    provider = extraction.fields[0]
    assert provider.citation.source_type is SourceType.REFERRAL
    assert provider.citation.source_id == "docref-7"
    assert provider.citation.quote_or_value.startswith("From: Amara Okafor")
    # Ungrounded field stays visible with a null value, never invented.
    assert extraction.fields[2].value is None


@pytest.mark.anyio
async def test_extract_referral_forces_the_referral_tool():
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="record_referral_fields", input=REFERRAL_INPUT)
        ])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    extraction = await VisionExtractor(client).extract_referral(
        document_id="docref-7", patient_id="pat-1",
        media_type="application/pdf", data_b64="ZmFrZQ==",
    )
    assert captured["tool_choice"] == {"type": "tool", "name": "record_referral_fields"}
    assert extraction.fields[1].value == "hospice evaluation for advanced heart failure"


@pytest.fixture
def harness():
    app = create_app()
    store = InMemoryDocumentStore()
    app.dependency_overrides[doc_routes.get_document_extractor] = _fake_extractor
    app.dependency_overrides[doc_routes.get_document_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False), store


def test_upload_referral_returns_cited_fields(harness):
    client, store = harness
    response = client.post(
        "/documents",
        files={"file": ("referral.pdf", b"%PDF-1.4 referral", "application/pdf")},
        data={"patient_id": "pat-1", "doc_type": "referral"},
        headers={"X-Clinician-Id": "nurse-maria"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["doc_type"] == "referral"
    assert body["result_count"] == 3
    assert body["results"] == []
    field = body["fields"][0]
    assert field["field_name"] == "referring_provider"
    assert field["citation"]["source_type"] == "referral"
    assert field["citation"]["source_id"] == body["document_id"]
    assert len(store.get_patient_extractions("pat-1")) == 1


def test_ask_grounds_in_referral_facts(harness):
    client, _ = harness
    assert client.post(
        "/documents",
        files={"file": ("referral.pdf", b"%PDF-1.4 referral", "application/pdf")},
        data={"patient_id": "pat-1", "doc_type": "referral"},
        headers={"X-Clinician-Id": "nurse-maria"},
    ).status_code == 200

    response = client.post(
        "/ask",
        json={"patient_id": "pat-1", "question": "Why was this patient referred?"},
        headers={"X-Clinician-Id": "nurse-maria"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False
    assert "hospice evaluation" in body["answer"]
    assert any(c["source_type"] == "referral" for c in body["citations"])
    # The critic reviewed the referral-grounded answer too.
    assert [r["worker"] for r in body["routing"]] == ["intake", "evidence", "answer", "critic"]
    assert body["critic_flags"] == []
