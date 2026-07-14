"""Vision extractor — structured output at the boundary, lineage stamped by us.

Uses a fake Anthropic client so no network/model is involved: we assert the
draft is validated, the citation source_id is forced to the real document id
(never the model's), and the document/image block is shaped correctly.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.documents.extractor import ExtractionError, VisionExtractor
from app.documents.schemas import AbnormalFlag, SourceType

DRAFT_INPUT = {
    "collection_date": "2026-07-01",
    "results": [
        {
            "test_name": "Hemoglobin A1c",
            "value": "6.7",
            "unit": "%",
            "reference_range": "4.0-5.6",
            "abnormal_flag": "high",
            "confidence": "high",
            "page": 1,
            "quote": "A1c 6.7 %",
            "bbox": {"page": 1, "x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.25},
        }
    ],
}


class FakeVisionClient:
    def __init__(self, tool_input: dict | None = DRAFT_INPUT):
        self._tool_input = tool_input
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = []
        if self._tool_input is not None:
            content.append(
                SimpleNamespace(type="tool_use", name="record_lab_results", input=self._tool_input)
            )
        else:
            content.append(SimpleNamespace(type="text", text="I couldn't read it."))
        return SimpleNamespace(content=content)


@pytest.mark.anyio
async def test_extracts_and_stamps_lineage():
    client = FakeVisionClient()
    extraction = await VisionExtractor(client).extract_lab(
        document_id="docref-42", patient_id="pat-1",
        media_type="application/pdf", data_b64="ZmFrZQ==",
    )
    assert extraction.document_id == "docref-42"
    (result,) = extraction.results
    assert result.test_name == "Hemoglobin A1c"
    assert result.abnormal_flag is AbnormalFlag.HIGH
    # Lineage the model never supplied is stamped to the real document.
    assert result.citation.source_id == "docref-42"
    assert result.citation.source_type is SourceType.LAB_PDF
    assert result.citation.quote_or_value == "A1c 6.7 %"
    assert result.bbox.page == 1


@pytest.mark.anyio
async def test_forces_the_extraction_tool_with_a_pdf_document_block():
    client = FakeVisionClient()
    await VisionExtractor(client).extract_lab(
        document_id="d1", patient_id="p1", media_type="application/pdf", data_b64="ZmFrZQ==",
    )
    kwargs = client.calls[0]
    assert kwargs["tool_choice"] == {"type": "tool", "name": "record_lab_results"}
    block = kwargs["messages"][0]["content"][0]
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


@pytest.mark.anyio
async def test_image_media_type_uses_an_image_block():
    client = FakeVisionClient()
    await VisionExtractor(client).extract_lab(
        document_id="d1", patient_id="p1", media_type="image/png", data_b64="ZmFrZQ==",
    )
    block = client.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "image"


@pytest.mark.anyio
async def test_unsupported_media_type_raises():
    with pytest.raises(ExtractionError):
        await VisionExtractor(FakeVisionClient()).extract_lab(
            document_id="d1", patient_id="p1", media_type="text/plain", data_b64="ZmFrZQ==",
        )


@pytest.mark.anyio
async def test_no_tool_use_raises_extraction_error():
    with pytest.raises(ExtractionError):
        await VisionExtractor(FakeVisionClient(tool_input=None)).extract_lab(
            document_id="d1", patient_id="p1", media_type="application/pdf", data_b64="ZmFrZQ==",
        )
