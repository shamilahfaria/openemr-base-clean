"""POST /ask — answer a question via the multi-agent graph.

The request is handed to a LangGraph supervisor that routes across workers
(intake-extractor -> evidence-retriever -> answerer) over a typed state, with
every routing decision logged. The answer grounds in the patient's extracted
document facts; patient-record facts and guideline evidence are returned
separately. With nothing grounded to say, it degrades rather than inventing.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from .documents.ingest import DocumentStore
from .documents.routes import get_document_store
from .documents.schemas import DocumentCitation, LabResult
from .graph.build import build_graph
from .graph.state import AgentState, RoutingDecision
from .middleware import get_correlation_id

logger = logging.getLogger(__name__)

router = APIRouter()


class AskRequest(BaseModel):
    patient_id: str
    question: str


class AskResponse(BaseModel):
    answer: str
    citations: list[DocumentCitation]
    patient_facts: list[LabResult]
    guideline_evidence: list[dict]      # populated by hybrid RAG (Early-sub)
    degraded: bool
    routing: list[RoutingDecision]      # supervisor decisions — inspectable
    correlation_id: str


def _clinician(x_clinician_id: str = Header("")) -> str:
    if not x_clinician_id.strip():
        raise HTTPException(status_code=401, detail="clinician identity required")
    return x_clinician_id.strip()


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    clinician_id: str = Depends(_clinician),
    store: DocumentStore = Depends(get_document_store),
) -> AskResponse:
    correlation_id = get_correlation_id()
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="question is required")

    graph = build_graph(store)
    result = await graph.ainvoke(
        AgentState(patient_id=request.patient_id, question=request.question)
    )

    logger.info(
        "ask correlation_id=%s outcome=%s facts=%d route=%s",
        correlation_id,
        "degraded" if result["degraded"] else "answered",
        len(result["facts"]),
        "->".join(decision.worker for decision in result["routing"]),
    )

    return AskResponse(
        answer=result["answer"],
        citations=result["citations"],
        patient_facts=result["facts"],
        guideline_evidence=result["evidence"],
        degraded=result["degraded"],
        routing=result["routing"],
        correlation_id=correlation_id,
    )
