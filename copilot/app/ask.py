"""POST /ask — answer a question grounded in the patient's extracted documents.

MVP answerer: it composes the answer *deterministically* from the validated
extraction facts, so every clinical statement resolves to a document citation
and the whole path runs in CI with no live model. Patient-record facts and
guideline evidence are returned as separate lists (guideline evidence arrives
with hybrid RAG in Early-sub). With nothing grounded to say, it degrades rather
than inventing an answer.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from .documents.routes import get_document_store
from .documents.ingest import DocumentStore
from .documents.schemas import AbnormalFlag, DocumentCitation, LabResult
from .middleware import get_correlation_id

logger = logging.getLogger(__name__)

router = APIRouter()

_UNREMARKABLE = {AbnormalFlag.NORMAL, AbnormalFlag.UNKNOWN}


class AskRequest(BaseModel):
    patient_id: str
    question: str


class AskResponse(BaseModel):
    answer: str
    citations: list[DocumentCitation]
    patient_facts: list[LabResult]
    guideline_evidence: list[dict]     # populated by hybrid RAG (Early-sub)
    degraded: bool
    correlation_id: str


def _clinician(x_clinician_id: str = Header("")) -> str:
    if not x_clinician_id.strip():
        raise HTTPException(status_code=401, detail="clinician identity required")
    return x_clinician_id.strip()


def compose_answer(facts: list[LabResult]) -> str:
    lines = ["Based on the patient's recent documents:"]
    for fact in facts:
        unit = f" {fact.unit}" if fact.unit else ""
        flag = "" if fact.abnormal_flag in _UNREMARKABLE else f" ({fact.abnormal_flag.value})"
        lines.append(f"- {fact.test_name}: {fact.value}{unit}{flag}")
    return "\n".join(lines)


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    clinician_id: str = Depends(_clinician),
    store: DocumentStore = Depends(get_document_store),
) -> AskResponse:
    correlation_id = get_correlation_id()
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="question is required")

    # Grounded facts only: a value the extractor could actually read.
    facts = [
        result
        for extraction in store.get_patient_extractions(request.patient_id)
        for result in extraction.results
        if result.value is not None
    ]

    if not facts:
        logger.info(
            "ask correlation_id=%s outcome=degraded facts=0", correlation_id
        )
        return AskResponse(
            answer="No documents are available for this patient to answer that question.",
            citations=[],
            patient_facts=[],
            guideline_evidence=[],
            degraded=True,
            correlation_id=correlation_id,
        )

    logger.info(
        "ask correlation_id=%s outcome=answered facts=%d", correlation_id, len(facts)
    )
    return AskResponse(
        answer=compose_answer(facts),
        citations=[fact.citation for fact in facts],
        patient_facts=facts,
        guideline_evidence=[],
        degraded=False,
        correlation_id=correlation_id,
    )
