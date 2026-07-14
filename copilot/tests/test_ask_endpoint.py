"""POST /ask — grounded, cited answers; safe refusal on missing data."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.documents import routes as doc_routes
from app.documents.ingest import InMemoryDocumentStore
from app.documents.schemas import (
    AbnormalFlag,
    Confidence,
    DocumentCitation,
    LabReportExtraction,
    LabResult,
    SourceType,
)
from app.main import create_app


def _extraction(patient_id: str) -> LabReportExtraction:
    citation = DocumentCitation(
        source_type=SourceType.LAB_PDF, source_id="docref-1",
        page_or_section="1", field_or_chunk_id="a1c", quote_or_value="A1c 6.7 %",
    )
    return LabReportExtraction(
        document_id="docref-1", patient_id=patient_id,
        results=[LabResult(
            test_name="Hemoglobin A1c", value="6.7", unit="%",
            abnormal_flag=AbnormalFlag.HIGH, confidence=Confidence.HIGH, citation=citation,
        )],
    )


@pytest.fixture
def harness():
    app = create_app()
    store = InMemoryDocumentStore()
    app.dependency_overrides[doc_routes.get_document_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False), store


def _ask(client, patient_id="pat-1", question="What changed?"):
    return client.post(
        "/ask", json={"patient_id": patient_id, "question": question},
        headers={"X-Clinician-Id": "nurse-maria"},
    )


def test_answer_is_grounded_and_cited(harness):
    client, store = harness
    store._extractions["docref-1"] = _extraction("pat-1")
    response = _ask(client)
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False
    assert "A1c" in body["answer"]
    assert "normal" not in body["answer"].lower()
    # Every citation resolves to the source document.
    assert body["citations"]
    assert all(c["source_id"] == "docref-1" for c in body["citations"])
    assert all(c["source_type"] == "lab_pdf" for c in body["citations"])
    assert len(body["patient_facts"]) == 1


def test_missing_data_degrades_instead_of_inventing(harness):
    client, _ = harness
    response = _ask(client, patient_id="pat-nobody")
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["citations"] == []
    assert "no documents" in body["answer"].lower()


def test_missing_clinician_is_401(harness):
    client, _ = harness
    assert client.post("/ask", json={"patient_id": "p", "question": "q"}).status_code == 401


def test_blank_question_is_422(harness):
    client, store = harness
    store._extractions["docref-1"] = _extraction("pat-1")
    assert _ask(client, question="   ").status_code == 422
