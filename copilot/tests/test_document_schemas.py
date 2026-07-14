"""Extraction schema validation — the schema is the source of truth (FR-2.4).

Pins the guarantees the ingestion path relies on: unknown/hallucinated fields
are rejected, every fact carries a citation, enums and bbox bounds are enforced,
and an ungrounded field stays visible (confidence=low + null value) instead of
being invented.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.documents.schemas import (
    AbnormalFlag,
    BoundingBox,
    Confidence,
    DocumentCitation,
    LabReportExtraction,
    LabResult,
    SourceType,
)


def _citation(**overrides) -> dict:
    base = {
        "source_type": "lab_pdf",
        "source_id": "docref-1",
        "page_or_section": "1",
        "field_or_chunk_id": "a1c",
        "quote_or_value": "6.7 %",
    }
    base.update(overrides)
    return base


def _result(**overrides) -> dict:
    base = {
        "test_name": "Hemoglobin A1c",
        "value": "6.7",
        "unit": "%",
        "reference_range": "4.0-5.6",
        "abnormal_flag": "high",
        "collection_date": "2026-07-01",
        "confidence": "high",
        "citation": _citation(),
        "bbox": {"page": 1, "x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.25},
    }
    base.update(overrides)
    return base


def test_valid_lab_extraction_parses():
    extraction = LabReportExtraction.model_validate(
        {"document_id": "docref-1", "patient_id": "pat-1", "results": [_result()]}
    )
    assert extraction.results[0].test_name == "Hemoglobin A1c"
    assert extraction.results[0].abnormal_flag is AbnormalFlag.HIGH
    assert extraction.results[0].citation.source_type is SourceType.LAB_PDF
    assert extraction.results[0].bbox.page == 1


def test_hallucinated_field_is_rejected():
    # extra="forbid": a field the schema doesn't know about must fail, not pass through.
    with pytest.raises(ValidationError):
        LabResult.model_validate(_result(diagnosis="prediabetes"))


def test_unknown_abnormal_flag_is_rejected():
    with pytest.raises(ValidationError):
        LabResult.model_validate(_result(abnormal_flag="slightly-elevated"))


def test_every_result_requires_a_citation():
    payload = _result()
    del payload["citation"]
    with pytest.raises(ValidationError):
        LabResult.model_validate(payload)


def test_ungrounded_field_stays_visible_not_invented():
    # The model couldn't read the value: low confidence + null value is valid.
    record = LabResult.model_validate(
        _result(value=None, confidence="low", bbox=None)
    )
    assert record.value is None
    assert record.confidence is Confidence.LOW


def test_bbox_coordinates_are_bounded_to_unit_square():
    with pytest.raises(ValidationError):
        BoundingBox.model_validate({"page": 1, "x0": 0.1, "y0": 0.2, "x1": 1.4, "y1": 0.3})
    with pytest.raises(ValidationError):
        BoundingBox.model_validate({"page": 0, "x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.3})


def test_citation_rejects_unknown_source_type():
    with pytest.raises(ValidationError):
        DocumentCitation.model_validate(_citation(source_type="scanned_thing"))
