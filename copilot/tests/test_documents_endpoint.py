"""POST /documents — upload -> grounded, cited extraction."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.documents import routes as doc_routes
from app.documents.extractor import VisionExtractor
from app.documents.ingest import InMemoryDocumentStore
from app.main import create_app

TOOL_INPUT = {
    "collection_date": "2026-07-01",
    "results": [
        {"test_name": "Hemoglobin A1c", "value": "6.7", "unit": "%", "confidence": "high",
         "abnormal_flag": "high", "page": 1, "quote": "A1c 6.7 %",
         "bbox": {"page": 1, "x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.25}}
    ],
}


def _fake_extractor() -> VisionExtractor:
    async def create(**kwargs):
        return SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="record_lab_results", input=TOOL_INPUT)
        ])
    return VisionExtractor(SimpleNamespace(messages=SimpleNamespace(create=create)))


@pytest.fixture
def harness():
    app = create_app()
    store = InMemoryDocumentStore()
    app.dependency_overrides[doc_routes.get_document_extractor] = _fake_extractor
    app.dependency_overrides[doc_routes.get_document_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False), store


def _upload(client, **overrides):
    kw = dict(
        files={"file": ("labs.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"patient_id": "pat-1", "doc_type": "lab_pdf"},
        headers={"X-Clinician-Id": "nurse-maria"},
    )
    kw.update(overrides)
    return client.post("/documents", **kw)


def test_upload_returns_grounded_cited_extraction(harness):
    client, store = harness
    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["result_count"] == 1
    result = body["results"][0]
    assert result["test_name"] == "Hemoglobin A1c"
    assert result["abnormal_flag"] == "high"
    # Citation anchored to the stored document; bbox present for the overlay.
    assert result["citation"]["source_id"] == body["document_id"]
    assert result["citation"]["source_type"] == "lab_pdf"
    assert result["bbox"]["page"] == 1
    # It landed in the store, retrievable for grounding answers.
    assert len(store.get_patient_extractions("pat-1")) == 1


def test_missing_clinician_is_401(harness):
    client, _ = harness
    assert _upload(client, headers={}).status_code == 401


def test_unsupported_doc_type_is_422(harness):
    client, _ = harness
    assert _upload(client, data={"patient_id": "pat-1", "doc_type": "xray"}).status_code == 422


def test_empty_upload_is_422(harness):
    client, _ = harness
    response = _upload(client, files={"file": ("empty.pdf", b"", "application/pdf")})
    assert response.status_code == 422


def test_correlation_id_is_echoed(harness):
    client, _ = harness
    response = _upload(client)
    assert response.json()["correlation_id"] == response.headers["X-Correlation-ID"]
