"""Strict extraction schemas — the source of truth for document ingestion.

Raw vision-model output is forced through these models with ``extra="forbid"``,
so an unexpected or hallucinated field fails validation rather than silently
entering the record (FR-2.1). Fields the model cannot ground are emitted with
``confidence=low`` and a null value — visible, never invented (FR-1.6).

Every derived fact carries a machine-readable ``DocumentCitation`` (FR-5.1) and,
where the model located it on the page, a ``BoundingBox`` captured at extraction
time — this is what powers the citation overlay (designed in, not bolted on).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    LAB_PDF = "lab_pdf"
    INTAKE_FORM = "intake_form"
    REFERRAL = "referral"
    FHIR = "fhir"
    GUIDELINE = "guideline"


class AbnormalFlag(str, Enum):
    NORMAL = "normal"
    LOW = "low"
    HIGH = "high"
    CRITICAL = "critical"
    ABNORMAL = "abnormal"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BoundingBox(BaseModel):
    """Normalized [0,1] page coordinates (top-left origin) for the overlay."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)


class DocumentCitation(BaseModel):
    """Machine-readable citation contract (FR-5.1)."""

    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    source_id: str                       # DocumentReference id — the lineage anchor
    page_or_section: str | None = None
    field_or_chunk_id: str | None = None
    quote_or_value: str | None = None


class LabResult(BaseModel):
    """One extracted lab value. ``value`` stays a string to preserve what the
    report actually said ("5.2", "positive", "<0.01") without lossy coercion."""

    model_config = ConfigDict(extra="forbid")

    test_name: str
    value: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    abnormal_flag: AbnormalFlag = AbnormalFlag.UNKNOWN
    collection_date: str | None = None   # ISO 8601 date
    confidence: Confidence
    citation: DocumentCitation
    bbox: BoundingBox | None = None


class LabReportExtraction(BaseModel):
    """The validated result of extracting a ``lab_pdf`` (FR-2.2)."""

    model_config = ConfigDict(extra="forbid")

    document_id: str                     # source DocumentReference id
    patient_id: str
    collection_date: str | None = None
    results: list[LabResult] = Field(default_factory=list)


class IntakeField(BaseModel):
    """One extracted intake-form field. ``value`` stays a string to preserve
    exactly what the form said ("penicillin — rash", "1/2 ppd x 10 yrs")."""

    model_config = ConfigDict(extra="forbid")

    field_name: str                      # e.g. "chief_complaint", "allergy"
    value: str | None = None
    section: str | None = None           # form section, e.g. "Allergies"
    confidence: Confidence
    citation: DocumentCitation
    bbox: BoundingBox | None = None


class IntakeFormExtraction(BaseModel):
    """The validated result of extracting an ``intake_form`` (FR-2.2)."""

    model_config = ConfigDict(extra="forbid")

    document_id: str                     # source DocumentReference id
    patient_id: str
    form_date: str | None = None
    fields: list[IntakeField] = Field(default_factory=list)


class ReferralExtraction(BaseModel):
    """The validated result of extracting a ``referral`` fax/letter. Shares the
    sectioned-field shape with intake forms (referrals are form-like), with its
    own source type so citations name the true document class."""

    model_config = ConfigDict(extra="forbid")

    document_id: str                     # source DocumentReference id
    patient_id: str
    referral_date: str | None = None
    fields: list[IntakeField] = Field(default_factory=list)


# Any validated extraction shape — what stores and the graph consume.
Extraction = LabReportExtraction | IntakeFormExtraction | ReferralExtraction


# --- Model-facing draft shapes (parse, don't validate) ---------------------
# The vision model fills a draft: the facts plus *where* it found each one
# (page + quote). It never supplies source_id — the ingestion tool stamps the
# real DocumentReference id, so provenance cannot be forged by the model.


class LabResultDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_name: str
    value: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    abnormal_flag: AbnormalFlag = AbnormalFlag.UNKNOWN
    collection_date: str | None = None
    confidence: Confidence
    page: int | None = None              # where on the document it was read
    quote: str | None = None             # verbatim snippet supporting the value
    bbox: BoundingBox | None = None


class LabExtractionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection_date: str | None = None
    results: list[LabResultDraft] = Field(default_factory=list)


def finalize_lab_extraction(
    draft: LabExtractionDraft, *, document_id: str, patient_id: str
) -> LabReportExtraction:
    """Stamp lineage onto a model draft: every result's citation is anchored to
    the real ``document_id`` (source_type=lab_pdf), which the model never sees."""
    results = [
        LabResult(
            test_name=row.test_name,
            value=row.value,
            unit=row.unit,
            reference_range=row.reference_range,
            abnormal_flag=row.abnormal_flag,
            collection_date=row.collection_date or draft.collection_date,
            confidence=row.confidence,
            citation=DocumentCitation(
                source_type=SourceType.LAB_PDF,
                source_id=document_id,
                page_or_section=str(row.page) if row.page is not None else None,
                field_or_chunk_id=row.test_name,
                quote_or_value=row.quote,
            ),
            bbox=row.bbox,
        )
        for row in draft.results
    ]
    return LabReportExtraction(
        document_id=document_id,
        patient_id=patient_id,
        collection_date=draft.collection_date,
        results=results,
    )


class IntakeFieldDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    value: str | None = None
    section: str | None = None
    confidence: Confidence
    page: int | None = None              # where on the document it was read
    quote: str | None = None             # verbatim snippet supporting the value
    bbox: BoundingBox | None = None


class IntakeExtractionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form_date: str | None = None
    fields: list[IntakeFieldDraft] = Field(default_factory=list)


def _finalize_fields(
    rows: list[IntakeFieldDraft], *, source_type: SourceType, document_id: str
) -> list[IntakeField]:
    """Stamp lineage onto sectioned-field drafts: every field's citation is
    anchored to the real ``document_id``, which the model never sees."""
    return [
        IntakeField(
            field_name=row.field_name,
            value=row.value,
            section=row.section,
            confidence=row.confidence,
            citation=DocumentCitation(
                source_type=source_type,
                source_id=document_id,
                page_or_section=row.section or (str(row.page) if row.page is not None else None),
                field_or_chunk_id=row.field_name,
                quote_or_value=row.quote,
            ),
            bbox=row.bbox,
        )
        for row in rows
    ]


def finalize_intake_extraction(
    draft: IntakeExtractionDraft, *, document_id: str, patient_id: str
) -> IntakeFormExtraction:
    return IntakeFormExtraction(
        document_id=document_id,
        patient_id=patient_id,
        form_date=draft.form_date,
        fields=_finalize_fields(
            draft.fields, source_type=SourceType.INTAKE_FORM, document_id=document_id
        ),
    )


class ReferralExtractionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    referral_date: str | None = None
    fields: list[IntakeFieldDraft] = Field(default_factory=list)


def finalize_referral_extraction(
    draft: ReferralExtractionDraft, *, document_id: str, patient_id: str
) -> ReferralExtraction:
    return ReferralExtraction(
        document_id=document_id,
        patient_id=patient_id,
        referral_date=draft.referral_date,
        fields=_finalize_fields(
            draft.fields, source_type=SourceType.REFERRAL, document_id=document_id
        ),
    )
