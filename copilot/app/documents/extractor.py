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

from .schemas import (
    IntakeExtractionDraft,
    IntakeFormExtraction,
    LabExtractionDraft,
    LabReportExtraction,
    ReferralExtraction,
    ReferralExtractionDraft,
    finalize_intake_extraction,
    finalize_lab_extraction,
    finalize_referral_extraction,
)

logger = logging.getLogger(__name__)

_LAB_TOOL = "record_lab_results"

_LAB_SYSTEM = (
    "You are a clinical document extractor. Read the attached lab report and "
    "record every result you can see using the record_lab_results tool. Extract "
    "only what is present on the page — never infer or invent a value. For any "
    "field you cannot read confidently, set confidence to \"low\" and leave the "
    "value null. For each result include the page number and a short verbatim "
    "quote of the text the value came from."
)

_REFERRAL_TOOL = "record_referral_fields"

_REFERRAL_SYSTEM = (
    "You are a clinical document extractor. Read the attached referral "
    "letter/fax and record every completed field you can see using the "
    "record_referral_fields tool (referring provider and organization, reason "
    "for referral, relevant diagnoses, requested service, current medications "
    "mentioned, and so on). Extract only what is written on the document — "
    "never infer or invent a value; leave blank fields out or set confidence "
    "to \"low\" with a null value when unreadable. For each field include the "
    "document section, the page number, and a short verbatim quote of the "
    "text the value came from."
)

_INTAKE_TOOL = "record_intake_fields"

_INTAKE_SYSTEM = (
    "You are a clinical document extractor. Read the attached patient intake "
    "form and record every completed field you can see using the "
    "record_intake_fields tool (chief complaint, history, medications, "
    "allergies, social history, and so on). Extract only what is written on "
    "the form — never infer or invent a value; leave blank fields out or set "
    "confidence to \"low\" with a null value when unreadable. For each field "
    "include the form section, the page number, and a short verbatim quote of "
    "the text the value came from."
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

    async def _extract(
        self,
        *,
        tool_name: str,
        tool_description: str,
        draft_schema: dict,
        system: str,
        instruction: str,
        media_type: str,
        data_b64: str,
    ) -> Any:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        _document_block(media_type, data_b64),
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            tools=[{
                "name": tool_name,
                "description": tool_description,
                "input_schema": draft_schema,
            }],
            tool_choice={"type": "tool", "name": tool_name},
        )

        tool_input = next(
            (
                block.input
                for block in response.content
                if getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == tool_name
            ),
            None,
        )
        if tool_input is None:
            raise ExtractionError("model returned no structured extraction")
        return tool_input

    async def extract_lab(
        self,
        *,
        document_id: str,
        patient_id: str,
        media_type: str,
        data_b64: str,
    ) -> LabReportExtraction:
        tool_input = await self._extract(
            tool_name=_LAB_TOOL,
            tool_description="Record the lab results read from the document.",
            draft_schema=LabExtractionDraft.model_json_schema(),
            system=_LAB_SYSTEM,
            instruction="Extract the lab results.",
            media_type=media_type,
            data_b64=data_b64,
        )
        # Validate at the boundary; then stamp lineage the model never controls.
        draft = LabExtractionDraft.model_validate(tool_input)
        return finalize_lab_extraction(
            draft, document_id=document_id, patient_id=patient_id
        )

    async def extract_referral(
        self,
        *,
        document_id: str,
        patient_id: str,
        media_type: str,
        data_b64: str,
    ) -> ReferralExtraction:
        tool_input = await self._extract(
            tool_name=_REFERRAL_TOOL,
            tool_description="Record the referral fields read from the document.",
            draft_schema=ReferralExtractionDraft.model_json_schema(),
            system=_REFERRAL_SYSTEM,
            instruction="Extract the referral fields.",
            media_type=media_type,
            data_b64=data_b64,
        )
        # Validate at the boundary; then stamp lineage the model never controls.
        draft = ReferralExtractionDraft.model_validate(tool_input)
        return finalize_referral_extraction(
            draft, document_id=document_id, patient_id=patient_id
        )

    async def extract_intake(
        self,
        *,
        document_id: str,
        patient_id: str,
        media_type: str,
        data_b64: str,
    ) -> IntakeFormExtraction:
        tool_input = await self._extract(
            tool_name=_INTAKE_TOOL,
            tool_description="Record the intake-form fields read from the document.",
            draft_schema=IntakeExtractionDraft.model_json_schema(),
            system=_INTAKE_SYSTEM,
            instruction="Extract the intake form fields.",
            media_type=media_type,
            data_b64=data_b64,
        )
        # Validate at the boundary; then stamp lineage the model never controls.
        draft = IntakeExtractionDraft.model_validate(tool_input)
        return finalize_intake_extraction(
            draft, document_id=document_id, patient_id=patient_id
        )
