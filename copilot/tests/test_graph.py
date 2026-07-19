"""Multi-agent graph — explicit, logged routing over a typed state."""
from __future__ import annotations

import pytest

from app.documents.ingest import InMemoryDocumentStore
from app.documents.schemas import (
    AbnormalFlag,
    Confidence,
    DocumentCitation,
    LabReportExtraction,
    LabResult,
    SourceType,
)
from app.graph.build import build_graph
from app.graph.state import AgentState


def _store_with_a1c() -> InMemoryDocumentStore:
    store = InMemoryDocumentStore()
    citation = DocumentCitation(source_type=SourceType.LAB_PDF, source_id="docref-1")
    store._extractions["docref-1"] = LabReportExtraction(
        document_id="docref-1", patient_id="pat-1",
        results=[LabResult(
            test_name="Hemoglobin A1c", value="6.7", unit="%",
            abnormal_flag=AbnormalFlag.HIGH, confidence=Confidence.HIGH, citation=citation,
        )],
    )
    return store


@pytest.mark.anyio
async def test_supervisor_routes_intake_then_evidence_then_answer():
    graph = build_graph(_store_with_a1c())
    result = await graph.ainvoke(AgentState(patient_id="pat-1", question="what changed?"))
    assert [d.worker for d in result["routing"]] == ["intake", "evidence", "answer", "critic"]
    # Every decision is explainable — never a black box.
    assert all(d.reason for d in result["routing"])


@pytest.mark.anyio
async def test_answer_is_grounded_and_cited_via_graph():
    graph = build_graph(_store_with_a1c())
    result = await graph.ainvoke(AgentState(patient_id="pat-1", question="what changed?"))
    assert result["degraded"] is False
    assert "A1c" in result["answer"]
    doc_citations = [c for c in result["citations"] if c.source_type is SourceType.LAB_PDF]
    assert [c.source_id for c in doc_citations] == ["docref-1"]
    # The abnormal A1c also pulled cited guideline evidence (hybrid RAG).
    assert any(c.source_type is SourceType.GUIDELINE for c in result["citations"])
    assert len(result["facts"]) == 1


@pytest.mark.anyio
async def test_missing_data_degrades_via_graph():
    graph = build_graph(InMemoryDocumentStore())
    result = await graph.ainvoke(AgentState(patient_id="nobody", question="q"))
    assert result["degraded"] is True
    assert result["citations"] == []
    # It still routed through the workers before deciding it had nothing to say.
    assert [d.worker for d in result["routing"]] == ["intake", "evidence", "answer", "critic"]
