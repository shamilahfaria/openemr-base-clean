"""Hybrid RAG — keyword + dense retrieval, RRF fusion, coverage rerank.

Deterministic by construction (BM25 + stable-hash TF-IDF embeddings), so these
assertions hold offline and in CI with no model or index server.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.documents import routes as doc_routes
from app.documents.extractor import VisionExtractor
from app.documents.ingest import InMemoryDocumentStore
from app.main import create_app
from app.rag.retriever import default_retriever


@pytest.fixture(scope="module")
def retriever():
    return default_retriever()


def test_topical_query_ranks_the_right_chunk_first(retriever):
    hits = retriever.search("hemoglobin a1c 6.7 percent high")
    assert hits
    assert hits[0].chunk_id in {"ada-a1c-diagnosis", "ada-a1c-target"}


def test_both_channels_contribute(retriever):
    (top, *_rest) = retriever.search("elevated LDL cholesterol statin therapy")
    assert top.chunk_id == "lipid-ldl-elevated"
    # The winning hit was found by keyword AND dense retrieval, then fused.
    assert top.keyword_rank == 1
    assert top.dense_rank == 1
    assert top.fused_score > 0
    assert top.rerank_score >= top.fused_score


def test_irrelevant_query_returns_no_evidence(retriever):
    assert retriever.search("quantum entanglement spaceship warp drive") == []


def test_empty_query_returns_no_evidence(retriever):
    assert retriever.search("") == []
    assert retriever.search("the of and") == []       # stopwords only


def test_search_is_deterministic(retriever):
    a = retriever.search("penicillin allergy rash documented")
    b = retriever.search("penicillin allergy rash documented")
    assert [h.model_dump() for h in a] == [h.model_dump() for h in b]
    assert a[0].chunk_id == "penicillin-allergy"


def test_results_are_capped_at_k(retriever):
    assert len(retriever.search("blood pressure potassium sodium anemia", k=2)) == 2


# --- end to end through /ask -------------------------------------------------

LAB_INPUT = {
    "collection_date": "2026-07-01",
    "results": [
        {"test_name": "Hemoglobin A1c", "value": "6.7", "unit": "%",
         "confidence": "high", "abnormal_flag": "high", "page": 1, "quote": "A1c 6.7 %"}
    ],
}


def _fake_extractor() -> VisionExtractor:
    async def create(**kwargs):
        return SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="record_lab_results", input=LAB_INPUT)
        ])
    return VisionExtractor(SimpleNamespace(messages=SimpleNamespace(create=create)))


def test_ask_returns_reranked_guideline_evidence_with_citations():
    app = create_app()
    store = InMemoryDocumentStore()
    app.dependency_overrides[doc_routes.get_document_extractor] = _fake_extractor
    app.dependency_overrides[doc_routes.get_document_store] = lambda: store
    client = TestClient(app, raise_server_exceptions=False)

    assert client.post(
        "/documents",
        files={"file": ("labs.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"patient_id": "pat-1", "doc_type": "lab_pdf"},
        headers={"X-Clinician-Id": "nurse-maria"},
    ).status_code == 200

    response = client.post(
        "/ask",
        json={"patient_id": "pat-1", "question": "What changed in this patient's labs?"},
        headers={"X-Clinician-Id": "nurse-maria"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False

    # The abnormal A1c steered retrieval to A1c guidance, with provenance.
    evidence = body["guideline_evidence"]
    assert evidence
    assert any("a1c" in hit["chunk_id"] for hit in evidence)
    assert all("rerank_score" in hit and "fused_score" in hit for hit in evidence)

    # Evidence is cited in the answer and in the machine-readable citations.
    assert "Relevant guidance:" in body["answer"]
    guideline_citations = [c for c in body["citations"] if c["source_type"] == "guideline"]
    assert {c["source_id"] for c in guideline_citations} == {h["chunk_id"] for h in evidence}

    # Routing shows the full supervisor loop: intake -> evidence -> answer.
    assert [r["worker"] for r in body["routing"]] == ["intake", "evidence", "answer"]
