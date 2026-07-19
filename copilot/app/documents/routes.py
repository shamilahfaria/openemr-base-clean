"""POST /documents — upload a clinical document, extract grounded facts.

Multipart upload (file + patient_id + doc_type). The response returns the
validated extraction with per-fact citations and bounding boxes — grounded and
inspectable. Telemetry is PHI-free: correlation id, doc_type, result count,
latency — never a value or quote.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from ..middleware import get_correlation_id
from .extractor import ExtractionError, VisionExtractor
from .ingest import DocumentStore, attach_and_extract
from .schemas import IntakeField, LabReportExtraction, LabResult

logger = logging.getLogger(__name__)

router = APIRouter()

SUPPORTED_DOC_TYPES = {"lab_pdf", "intake_form", "referral"}


class DocumentIngestResponse(BaseModel):
    document_id: str
    patient_id: str
    doc_type: str
    result_count: int
    results: list[LabResult] = []        # lab_pdf extractions
    fields: list[IntakeField] = []       # intake_form extractions
    correlation_id: str


def get_document_extractor() -> VisionExtractor:
    raise NotImplementedError  # production wiring; overridden in tests


def get_document_store() -> DocumentStore:
    raise NotImplementedError  # production wiring; overridden in tests


def _clinician(x_clinician_id: str = Header("")) -> str:
    if not x_clinician_id.strip():
        raise HTTPException(status_code=401, detail="clinician identity required")
    return x_clinician_id.strip()


@router.post("/documents", response_model=DocumentIngestResponse)
async def ingest_document(
    file: UploadFile = File(...),
    patient_id: str = Form(...),
    doc_type: str = Form("lab_pdf"),
    clinician_id: str = Depends(_clinician),
    extractor: VisionExtractor = Depends(get_document_extractor),
    store: DocumentStore = Depends(get_document_store),
) -> DocumentIngestResponse:
    correlation_id = get_correlation_id()
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise HTTPException(status_code=422, detail=f"unsupported doc_type: {doc_type}")
    if not patient_id.strip():
        raise HTTPException(status_code=422, detail="patient_id is required")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="empty upload")
    media_type = file.content_type or "application/pdf"

    started = time.monotonic()
    try:
        extraction = await attach_and_extract(
            patient_id=patient_id.strip(),
            filename=file.filename or "upload",
            media_type=media_type,
            data=data,
            doc_type=doc_type,
            store=store,
            extractor=extractor,
        )
    except ExtractionError:
        logger.warning("doc_ingest extraction failed correlation_id=%s", correlation_id)
        raise HTTPException(status_code=422, detail="could not extract the document")

    is_lab = isinstance(extraction, LabReportExtraction)
    facts = extraction.results if is_lab else extraction.fields

    latency_ms = (time.monotonic() - started) * 1000
    # PHI-free: counts and ids only, never extracted values or quotes.
    logger.info(
        "doc_ingest correlation_id=%s doc_type=%s document_id=%s results=%d latency_ms=%.1f",
        correlation_id, doc_type, extraction.document_id, len(facts), latency_ms,
    )

    return DocumentIngestResponse(
        document_id=extraction.document_id,
        patient_id=extraction.patient_id,
        doc_type=doc_type,
        result_count=len(facts),
        results=extraction.results if is_lab else [],
        fields=[] if is_lab else extraction.fields,
        correlation_id=correlation_id,
    )
