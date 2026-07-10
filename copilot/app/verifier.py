"""Verification layer — STUB (contracts only).

The trust boundary (ARCHITECTURE.md "Verification (the crux)"): deterministic,
runs after the model's draft and before display. Two checks:

1. Source attribution. The model marks every sentence: ``[src: <id>]`` cites a
   retrieved record; ``[general]`` labels outside-record content. A sentence
   survives only if all its cited ids exist in this turn's retrieved records.
   Uncited sentences and unknown ids are WITHHELD (blocked) — prefer silence
   over invention. ``[general]`` sentences are kept but explicitly labeled
   "(not from the patient record)". Markers never reach the nurse; citations
   are returned structured.

2. Clinical rule checks, from a versioned rule set: allergy cross-check
   (retrieved meds vs the patient's own retrieved allergies, case-insensitive
   substring), curated interaction pairs, and dose thresholds. A violation
   FLAGS a warning; it does not block the answer. Unsupported claims BLOCK.

Known limits (deliberate): attribution is record-level, not sentence-exact;
the rule set is curated, not exhaustive; unparseable doses yield no dose
warning.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .orchestrator import TurnDraft

CITATION_RE = re.compile(r"\[src:\s*([^\]]+?)\s*\]")
GENERAL_MARKER = "[general]"
OUTSIDE_RECORD_LABEL = "(not from the patient record)"


class InteractionRule(BaseModel):
    drug_a: str
    drug_b: str
    note: str


class DoseLimitRule(BaseModel):
    drug: str
    max_single_dose_mg: float


class ClinicalRuleSet(BaseModel):
    version: str
    interactions: list[InteractionRule] = []
    dose_limits: list[DoseLimitRule] = []


class Citation(BaseModel):
    claim: str          # the sentence, markers stripped
    source_id: str


class VerificationResult(BaseModel):
    answer: str                 # surviving sentences, markers stripped
    citations: list[Citation]
    warnings: list[str]         # clinical-rule flags
    withheld: list[str]         # blocked sentences — audit trail, never shown
    rules_version: str

    @property
    def passed(self) -> bool:
        """False when nothing survived — the caller must take the fallback path."""
        return bool(self.answer.strip())


class Verifier:
    def __init__(self, rules: ClinicalRuleSet):
        raise NotImplementedError

    def verify(self, draft: TurnDraft) -> VerificationResult:
        raise NotImplementedError
