"""Graph nodes — supervisor + two workers + answerer.

Each worker wraps existing, already-tested logic (the document store, the
answer composer) as a graph node rather than reimplementing it. The supervisor
makes an explicit, logged routing decision each turn; handoffs are never hidden.
"""
from __future__ import annotations

import logging

from ..documents.ingest import DocumentStore
from ..documents.schemas import (
    AbnormalFlag,
    DocumentCitation,
    IntakeField,
    LabResult,
    LabReportExtraction,
    SourceType,
)
from ..rag.retriever import HybridRetriever
from .state import AgentState, RoutingDecision

logger = logging.getLogger(__name__)

_UNREMARKABLE = {AbnormalFlag.NORMAL, AbnormalFlag.UNKNOWN}


def compose_answer(facts: list[LabResult | IntakeField]) -> str:
    lines = ["Based on the patient's recent documents:"]
    for fact in facts:
        if isinstance(fact, LabResult):
            unit = f" {fact.unit}" if fact.unit else ""
            flag = "" if fact.abnormal_flag in _UNREMARKABLE else f" ({fact.abnormal_flag.value})"
            lines.append(f"- {fact.test_name}: {fact.value}{unit}{flag}")
        else:
            section = f" [{fact.section}]" if fact.section else ""
            lines.append(f"- {fact.field_name}{section}: {fact.value}")
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
        facts: list[LabResult | IntakeField] = []
        for extraction in store.get_patient_extractions(state.patient_id):
            items = (
                extraction.results
                if isinstance(extraction, LabReportExtraction)
                else extraction.fields
            )
            facts.extend(item for item in items if item.value is not None)
        logger.info("worker_intake facts=%d", len(facts))
        return {"facts": facts, "extracted": True}

    return intake_extractor


def make_evidence_retriever(retriever: HybridRetriever):
    """Worker: hybrid RAG (keyword BM25 + dense embeddings, RRF-fused, then
    reranked) over the guideline corpus. The query is the clinician's question
    expanded with the names of the patient's noteworthy facts, so retrieval
    follows what the record actually shows."""

    def evidence_retriever(state: AgentState) -> dict:
        terms = [state.question]
        for fact in state.facts:
            if isinstance(fact, LabResult):
                if fact.abnormal_flag not in _UNREMARKABLE:
                    terms.append(fact.test_name)
            elif fact.value is not None:
                terms.append(f"{fact.field_name} {fact.value}")
        hits = retriever.search(" ".join(terms))
        # PHI-free: chunk ids and counts only — never patient facts.
        logger.info(
            "worker_evidence hits=%d chunks=%s",
            len(hits), ",".join(h.chunk_id for h in hits),
        )
        return {"evidence": [hit.model_dump() for hit in hits], "retrieved": True}

    return evidence_retriever


def _evidence_citation(hit: dict) -> DocumentCitation:
    return DocumentCitation(
        source_type=SourceType.GUIDELINE,
        source_id=hit["chunk_id"],
        page_or_section=hit["section"],
        field_or_chunk_id=hit["chunk_id"],
        quote_or_value=hit["title"],
    )


def answerer(state: AgentState) -> dict:
    """Compose the grounded, cited answer — or degrade rather than invent."""
    if not state.facts:
        return {
            "answer": "No documents are available for this patient to answer that question.",
            "citations": [],
            "degraded": True,
        }
    answer = compose_answer(state.facts)
    if state.evidence:
        lines = ["", "Relevant guidance:"]
        lines += [
            f"- {hit['title']} ({hit['source']}) [guideline:{hit['chunk_id']}]"
            for hit in state.evidence
        ]
        answer += "\n".join(lines)
    return {
        "answer": answer,
        "citations": [
            *(fact.citation for fact in state.facts),
            *(_evidence_citation(hit) for hit in state.evidence),
        ],
        "degraded": False,
    }
