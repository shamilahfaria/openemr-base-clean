"""attach_and_extract — the ingestion flow (Stage 1).

Order matters: **store first, extract second.** The source document is persisted
and gets an id; that id becomes the ``source_id`` anchor for every derived fact,
so lineage is intact even if extraction later fails. Ingestion is idempotent by
(patient, content hash): re-uploading the same file returns the same document id
and never creates a duplicate record (FR-1.3).

The store is a protocol so the MVP in-memory implementation and a later OpenEMR
FHIR (DocumentReference + Observations) implementation are interchangeable — the
flow does not change.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Protocol

from .extractor import ExtractionError, VisionExtractor
from .schemas import Extraction


class StoredDocument:
    def __init__(self, document_id: str, patient_id: str, filename: str, media_type: str, content_hash: str):
        self.document_id = document_id
        self.patient_id = patient_id
        self.filename = filename
        self.media_type = media_type
        self.content_hash = content_hash


class DocumentStore(Protocol):
    async def store_document(
        self, *, patient_id: str, filename: str, media_type: str, data_b64: str, content_hash: str
    ) -> str:
        """Persist the source document; return its id. Idempotent by (patient, hash)."""
        ...

    async def store_extraction(self, extraction: Extraction) -> None:
        """Persist derived facts, linked to the source document id."""
        ...

    def get_patient_extractions(self, patient_id: str) -> list[Extraction]:
        """All extractions available for a patient (for grounding answers)."""
        ...


class InMemoryDocumentStore:
    """MVP store: keeps documents + extractions in process. Swappable for a FHIR
    store (DocumentReference + Observations) without touching the flow."""

    def __init__(self) -> None:
        self._docs: dict[str, StoredDocument] = {}
        self._by_hash: dict[tuple[str, str], str] = {}
        self._extractions: dict[str, Extraction] = {}

    async def store_document(
        self, *, patient_id: str, filename: str, media_type: str, data_b64: str, content_hash: str
    ) -> str:
        key = (patient_id, content_hash)
        if key in self._by_hash:
            return self._by_hash[key]                 # idempotent: same file -> same id
        # Scope the id to (patient, hash) so identical bytes for two patients
        # never collide onto the same document.
        document_id = "docref-" + hashlib.sha256(
            f"{patient_id}:{content_hash}".encode()
        ).hexdigest()[:16]
        self._docs[document_id] = StoredDocument(
            document_id, patient_id, filename, media_type, content_hash
        )
        self._by_hash[key] = document_id
        return document_id

    async def store_extraction(self, extraction: Extraction) -> None:
        self._extractions[extraction.document_id] = extraction

    def get_patient_extractions(self, patient_id: str) -> list[Extraction]:
        return [e for e in self._extractions.values() if e.patient_id == patient_id]

    def stats(self) -> dict[str, int]:
        """PHI-free counts for the readiness component walk."""
        return {"documents": len(self._docs), "extractions": len(self._extractions)}


async def attach_and_extract(
    *,
    patient_id: str,
    filename: str,
    media_type: str,
    data: bytes,
    doc_type: str = "lab_pdf",
    store: DocumentStore,
    extractor: VisionExtractor,
) -> Extraction:
    content_hash = hashlib.sha256(data).hexdigest()
    data_b64 = base64.b64encode(data).decode("ascii")

    document_id = await store.store_document(
        patient_id=patient_id,
        filename=filename,
        media_type=media_type,
        data_b64=data_b64,
        content_hash=content_hash,
    )
    if doc_type == "lab_pdf":
        extract = extractor.extract_lab
    elif doc_type == "intake_form":
        extract = extractor.extract_intake
    elif doc_type == "referral":
        extract = extractor.extract_referral
    else:
        raise ExtractionError(f"unsupported doc_type: {doc_type}")
    extraction = await extract(
        document_id=document_id,
        patient_id=patient_id,
        media_type=media_type,
        data_b64=data_b64,
    )
    await store.store_extraction(extraction)
    return extraction
