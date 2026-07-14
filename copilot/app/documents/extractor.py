"""Vision extraction: a document image/PDF -> a validated draft.

Claude reads the document and is *forced* to return its findings through the
``record_lab_results`` tool, whose input schema is the draft model — so the raw
model output is structured at the boundary and then validated (extra="forbid")
before anything downstream sees it. The Anthropic client is injected, so tests
run with a fake and never call the network.
"""
from __future__ import annotations

import logging
from typing import Any

from .schemas import LabExtractionDraft, LabReportExtraction, finalize_lab_extraction

logger = logging.getLogger(__name__)

_TOOL_NAME = "record_lab_results"

_SYSTEM = (
    "You are a clinical document extractor. Read the attached lab report and "
    "record every result you can see using the record_lab_results tool. Extract "
    "only what is present on the page — never infer or invent a value. For any "
    "field you cannot read confidently, set confidence to \"low\" and leave the "
    "value null. For each result include the page number and a short verbatim "
    "quote of the text the value came from."
)


class ExtractionError(Exception):
    """The model did not return a usable structured extraction."""


def _document_block(media_type: str, data_b64: str) -> dict:
    if media_type == "application/pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64},
        }
    if media_type.startswith("image/"):
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64},
        }
    raise ExtractionError(f"unsupported media type: {media_type}")


class VisionExtractor:
    def __init__(self, anthropic_client: Any, model: str = "claude-sonnet-4-5"):
        self._client = anthropic_client
        self._model = model

    def _tool_spec(self) -> dict:
        return {
            "name": _TOOL_NAME,
            "description": "Record the lab results read from the document.",
            "input_schema": LabExtractionDraft.model_json_schema(),
        }

    async def extract_lab(
        self,
        *,
        document_id: str,
        patient_id: str,
        media_type: str,
        data_b64: str,
    ) -> LabReportExtraction:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        _document_block(media_type, data_b64),
                        {"type": "text", "text": "Extract the lab results."},
                    ],
                }
            ],
            tools=[self._tool_spec()],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
        )

        tool_input = next(
            (
                block.input
                for block in response.content
                if getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == _TOOL_NAME
            ),
            None,
        )
        if tool_input is None:
            raise ExtractionError("model returned no structured extraction")

        # Validate at the boundary; then stamp lineage the model never controls.
        draft = LabExtractionDraft.model_validate(tool_input)
        return finalize_lab_extraction(
            draft, document_id=document_id, patient_id=patient_id
        )
