"""Graph nodes — supervisor + two workers + answerer.

Each worker wraps existing, already-tested logic (the document store, the
answer composer) as a graph node rather than reimplementing it. The supervisor
makes an explicit, logged routing decision each turn; handoffs are never hidden.
"""
from __future__ import annotations

import logging

from ..documents.ingest import DocumentStore
from ..documents.schemas import AbnormalFlag, LabResult
from .state import AgentState, RoutingDecision

logger = logging.getLogger(__name__)

_UNREMARKABLE = {AbnormalFlag.NORMAL, AbnormalFlag.UNKNOWN}


def compose_answer(facts: list[LabResult]) -> str:
    lines = ["Based on the patient's recent documents:"]
    for fact in facts:
        unit = f" {fact.unit}" if fact.unit else ""
        flag = "" if fact.abnormal_flag in _UNREMARKABLE else f" ({fact.abnormal_flag.value})"
        lines.append(f"- {fact.test_name}: {fact.value}{unit}{flag}")
    return "\n".join(lines)


def supervisor(state: AgentState) -> dict:
    """Decide the next worker from the state — and log the decision (PHI-free)."""
    if not state.extracted:
        worker, reason = "intake", "no patient facts gathered yet"
    elif not state.retrieved:
        worker, reason = "evidence", "have facts; check for guideline evidence"
    else:
        worker, reason = "answer", "enough grounded material to answer"
    logger.info("supervisor_route worker=%s reason=%s", worker, reason)
    return {"next": worker, "routing": [*state.routing, RoutingDecision(worker=worker, reason=reason)]}


def make_intake_extractor(store: DocumentStore):
    """Worker: gather the patient's already-extracted, grounded facts."""

    def intake_extractor(state: AgentState) -> dict:
        facts = [
            result
            for extraction in store.get_patient_extractions(state.patient_id)
            for result in extraction.results
            if result.value is not None
        ]
        logger.info("worker_intake facts=%d", len(facts))
        return {"facts": facts, "extracted": True}

    return intake_extractor


def evidence_retriever(state: AgentState) -> dict:
    """Worker: guideline evidence. Hybrid RAG + rerank lands in Early-sub; the
    node exists now so the graph shape is complete and routing is exercised."""
    logger.info("worker_evidence hits=0")
    return {"evidence": [], "retrieved": True}


def answerer(state: AgentState) -> dict:
    """Compose the grounded, cited answer — or degrade rather than invent."""
    if not state.facts:
        return {
            "answer": "No documents are available for this patient to answer that question.",
            "citations": [],
            "degraded": True,
        }
    return {
        "answer": compose_answer(state.facts),
        "citations": [fact.citation for fact in state.facts],
        "degraded": False,
    }
