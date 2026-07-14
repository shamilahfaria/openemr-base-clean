"""attach_and_extract — store-first lineage and idempotent ingestion."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.documents.extractor import VisionExtractor
from app.documents.ingest import InMemoryDocumentStore, attach_and_extract

TOOL_INPUT = {
    "collection_date": "2026-07-01",
    "results": [
        {"test_name": "Hemoglobin A1c", "value": "6.7", "unit": "%", "confidence": "high",
         "abnormal_flag": "high", "page": 1, "quote": "A1c 6.7 %"}
    ],
}


def _extractor() -> VisionExtractor:
    async def create(**kwargs):
        return SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="record_lab_results", input=TOOL_INPUT)
        ])
    return VisionExtractor(SimpleNamespace(messages=SimpleNamespace(create=create)))


@pytest.mark.anyio
async def test_stores_document_then_extraction_with_matching_lineage():
    store = InMemoryDocumentStore()
    extraction = await attach_and_extract(
        patient_id="pat-1", filename="labs.pdf", media_type="application/pdf",
        data=b"%PDF-1.4 fake", store=store, extractor=_extractor(),
    )
    # Extraction is anchored to the document the store minted.
    assert extraction.document_id.startswith("docref-")
    assert extraction.results[0].citation.source_id == extraction.document_id
    # And it's retrievable for grounding answers.
    stored = store.get_patient_extractions("pat-1")
    assert len(stored) == 1
    assert stored[0].document_id == extraction.document_id


@pytest.mark.anyio
async def test_reingesting_same_bytes_is_idempotent():
    store = InMemoryDocumentStore()
    first = await attach_and_extract(
        patient_id="pat-1", filename="labs.pdf", media_type="application/pdf",
        data=b"identical bytes", store=store, extractor=_extractor(),
    )
    second = await attach_and_extract(
        patient_id="pat-1", filename="labs-copy.pdf", media_type="application/pdf",
        data=b"identical bytes", store=store, extractor=_extractor(),
    )
    assert first.document_id == second.document_id
    assert len(store.get_patient_extractions("pat-1")) == 1  # no duplicate


@pytest.mark.anyio
async def test_different_patients_get_distinct_documents():
    store = InMemoryDocumentStore()
    a = await attach_and_extract(
        patient_id="pat-1", filename="l.pdf", media_type="application/pdf",
        data=b"same bytes", store=store, extractor=_extractor(),
    )
    b = await attach_and_extract(
        patient_id="pat-2", filename="l.pdf", media_type="application/pdf",
        data=b"same bytes", store=store, extractor=_extractor(),
    )
    assert a.document_id != b.document_id
