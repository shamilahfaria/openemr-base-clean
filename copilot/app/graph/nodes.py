"""Graph nodes — supervisor + workers + answerer + critic.

Each worker wraps existing, already-tested logic (the document store, the
answer composer) as a graph node rather than reimplementing it. The supervisor
makes an explicit, logged routing decision each turn; handoffs are never hidden.

The critic (FR-4.4) is deterministic by design: the canonical answer is a pure
function of the cited material in state, so the critic recomposes it and any
drift — uncited claims, action-suggestion language — is rejected and repaired,
never silently shipped.
"""
from __future__ import annotations

import logging
import re

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
    elif not state.answer:
        worker, reason = "answer", "enough grounded material to answer"
    else:
        worker, reason = "critic", "review the answer against its citations"
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


_REFUSAL = "No documents are available for this patient to answer that question."


def compose_canonical(
    facts: list[LabResult | IntakeField], evidence: list[dict]
) -> str:
    """The canonical answer — a pure function of the cited material. The
    answerer emits it; the critic recomputes it to verify nothing else
    slipped in."""
    if not facts:
        return _REFUSAL
    answer = compose_answer(facts)
    if evidence:
        lines = ["", "Relevant guidance:"]
        lines += [
            f"- {hit['title']} ({hit['source']}) [guideline:{hit['chunk_id']}]"
            for hit in evidence
        ]
        answer += "\n".join(lines)
    return answer


def answerer(state: AgentState) -> dict:
    """Compose the grounded, cited answer — or degrade rather than invent."""
    if not state.facts:
        return {"answer": _REFUSAL, "citations": [], "degraded": True}
    return {
        "answer": compose_canonical(state.facts, state.evidence),
        "citations": [
            *(fact.citation for fact in state.facts),
            *(_evidence_citation(hit) for hit in state.evidence),
        ],
        "degraded": False,
    }


# Read-only co-pilot: it reports the record, it never directs care. Lines the
# canonical composer did not produce are scanned for advice-shaped language so
# the rejection reason is accurate in the flags.
_UNSAFE_RE = re.compile(
    r"\b(you should|recommend|administer|prescribe|start|increase|decrease|"
    r"discontinue|titrate|hold|double|order)\b",
    re.IGNORECASE,
)


def critic(state: AgentState) -> dict:
    """Worker: reject uncited claims and unsafe advice (FR-4.4).

    Recomposes the canonical answer from the cited material in state and
    compares. Any line the citations cannot license is rejected — flagged as
    unsafe advice or an uncited claim — and the answer is replaced with the
    canonical, citation-only version. Deterministic: no model in the loop.
    """
    canonical = compose_canonical(state.facts, state.evidence)
    flags: list[str] = []
    if state.answer != canonical:
        canonical_lines = set(canonical.split("\n"))
        for line in state.answer.split("\n"):
            if line in canonical_lines or not line.strip():
                continue
            if _UNSAFE_RE.search(line):
                flags.append("unsafe action suggestion rejected (read-only co-pilot)")
            else:
                flags.append("uncited claim rejected (no supporting citation)")
        if not flags:
            # Canonical content is missing rather than extra — still repair.
            flags.append("answer diverged from cited material; recomposed")
    # PHI-free: counts only, never the rejected text.
    logger.info("worker_critic flags=%d", len(flags))
    return {"answer": canonical, "critic_flags": flags, "reviewed": True}
