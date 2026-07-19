"""Critic worker (FR-4.4 stretch) — rejects uncited claims and unsafe advice.

The critic is the fourth graph worker, running after the answerer. It is
deterministic: it recomposes the canonical answer purely from the cited
material in state (facts + guideline evidence). Any drift — content that is
not derived from a citation — is flagged and replaced, and action-suggestion
language is stripped (the co-pilot is read-only; it reports the record, it
never prescribes). Every response then carries `critic_flags`, and the
supervisor's routing shows the critic handoff explicitly.
"""
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
from app.graph.nodes import critic
from app.graph.state import AgentState, RoutingDecision


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


def _answered_state(answer: str) -> AgentState:
    """A state as it looks when the critic receives it from the answerer."""
    citation = DocumentCitation(source_type=SourceType.LAB_PDF, source_id="docref-1")
    fact = LabResult(
        test_name="Hemoglobin A1c", value="6.7", unit="%",
        abnormal_flag=AbnormalFlag.HIGH, confidence=Confidence.HIGH, citation=citation,
    )
    return AgentState(
        patient_id="pat-1", question="what changed?",
        facts=[fact], citations=[citation], evidence=[],
        answer=answer, degraded=False, extracted=True, retrieved=True,
        routing=[RoutingDecision(worker=w, reason="r") for w in ("intake", "evidence", "answer")],
    )


CANONICAL = "Based on the patient's recent documents:\n- Hemoglobin A1c: 6.7 % (high)"


# --- unit: the critic node ---------------------------------------------------


def test_clean_answer_passes_untouched():
    result = critic(_answered_state(CANONICAL))
    assert result["answer"] == CANONICAL
    assert result["critic_flags"] == []
    assert result["reviewed"] is True


def test_uncited_claim_is_rejected_and_answer_rebuilt():
    tampered = CANONICAL + "\n- Creatinine: 2.4 mg/dL (high)"   # no such cited fact
    result = critic(_answered_state(tampered))
    assert result["answer"] == CANONICAL
    assert any("uncited" in flag for flag in result["critic_flags"])


def test_unsafe_action_suggestion_is_rejected():
    tampered = CANONICAL + "\nYou should increase the metformin dose to 1000 mg."
    result = critic(_answered_state(tampered))
    assert "increase" not in result["answer"].lower()
    assert result["answer"] == CANONICAL
    assert result["critic_flags"]


def test_degraded_refusal_is_left_alone():
    state = _answered_state("No documents are available for this patient to answer that question.")
    state.facts = []
    state.citations = []
    state.degraded = True
    result = critic(state)
    assert result["critic_flags"] == []
    assert result["reviewed"] is True
    assert "No documents" in result["answer"]


# --- integration: the graph routes through the critic ------------------------


@pytest.mark.anyio
async def test_supervisor_routes_through_critic():
    graph = build_graph(_store_with_a1c())
    result = await graph.ainvoke(AgentState(patient_id="pat-1", question="what changed?"))
    assert [d.worker for d in result["routing"]] == ["intake", "evidence", "answer", "critic"]
    assert all(d.reason for d in result["routing"])
    assert result["reviewed"] is True
    assert result["critic_flags"] == []          # canonical answers are clean
    assert "A1c" in result["answer"]             # content survives review


@pytest.mark.anyio
async def test_degraded_path_also_routes_through_critic():
    graph = build_graph(InMemoryDocumentStore())
    result = await graph.ainvoke(AgentState(patient_id="nobody", question="q"))
    assert result["degraded"] is True
    assert [d.worker for d in result["routing"]] == ["intake", "evidence", "answer", "critic"]
