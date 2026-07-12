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

import logging
import re

from pydantic import BaseModel

from .orchestrator import TurnDraft
from .tools.chart import AllergyRecord, MedicationRecord

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_DOSE_MG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mg\b", re.IGNORECASE)

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


def _clean(sentence: str) -> str:
    """Strip markers and tidy the whitespace they leave behind."""
    text = CITATION_RE.sub("", sentence)
    text = text.replace(GENERAL_MARKER, "")
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([.!?,;:])", r"\1", text)
    return text.strip()


class Verifier:
    def __init__(self, rules: ClinicalRuleSet):
        self._rules = rules

    def verify(self, draft: TurnDraft) -> VerificationResult:
        valid_ids = {
            record.source_id
            for record in draft.retrieved
            if hasattr(record, "source_id")
        }

        kept: list[str] = []
        citations: list[Citation] = []
        withheld: list[str] = []
        warnings: list[str] = []

        uncited = 0
        unknown_source = 0

        def normalize(cited: str) -> str:
            """Match tolerantly, never loosely: models sometimes prefix the id
            with its FHIR resource type ("Observation/<id>"). Strip that prefix
            and compare case-insensitively — the id itself must still match a
            record retrieved THIS turn exactly."""
            bare = cited.rsplit("/", 1)[-1].strip().casefold()
            for valid in valid_ids:
                if valid.casefold() == bare:
                    return valid
            return cited

        sentences = [s for s in _SENTENCE_SPLIT_RE.split(draft.answer) if s.strip()]
        for sentence in sentences:
            cited_ids = [normalize(c) for c in CITATION_RE.findall(sentence)]
            claim = _clean(sentence)

            if cited_ids:
                if all(cid in valid_ids for cid in cited_ids):
                    kept.append(claim)
                    citations.extend(
                        Citation(claim=claim, source_id=cid) for cid in cited_ids
                    )
                else:
                    # Unknown source — the claim cannot be attributed. Block it.
                    withheld.append(claim)
                    unknown_source += 1
            elif GENERAL_MARKER in sentence:
                # Outside-record content is allowed but must be labeled.
                labeled = _clean(sentence)
                if labeled.endswith((".", "!", "?")):
                    labeled = f"{labeled[:-1]} {OUTSIDE_RECORD_LABEL}{labeled[-1]}"
                else:
                    labeled = f"{labeled} {OUTSIDE_RECORD_LABEL}"
                kept.append(labeled)
            else:
                # Uncited clinical content: prefer silence over invention.
                withheld.append(claim)
                uncited += 1

        if withheld:
            # PHI-free diagnostics: counts and reasons only, never claim text
            # or record ids (those live in the audit trail).
            logger.info(
                "verifier withheld=%d (uncited=%d unknown_source=%d) "
                "kept=%d valid_sources=%d",
                len(withheld),
                uncited,
                unknown_source,
                len(kept),
                len(valid_ids),
            )

        if withheld:
            warnings.append(
                f"{len(withheld)} statement(s) were withheld because they could "
                "not be verified against the patient record."
            )

        warnings.extend(self._clinical_warnings(draft.retrieved))

        return VerificationResult(
            answer=" ".join(kept),
            citations=citations,
            warnings=warnings,
            withheld=withheld,
            rules_version=self._rules.version,
        )

    def _clinical_warnings(self, retrieved: list) -> list[str]:
        meds = [r for r in retrieved if isinstance(r, MedicationRecord)]
        allergies = [r for r in retrieved if isinstance(r, AllergyRecord)]
        warnings: list[str] = []

        # 1. Allergy cross-check: the patient's own allergy list vs their meds.
        for med in meds:
            med_name = med.name.casefold()
            for allergy in allergies:
                substance = allergy.substance.casefold()
                if substance in med_name or med_name in substance:
                    warnings.append(
                        f"Allergy conflict: {med.name} matches documented "
                        f"allergy '{allergy.substance}'."
                    )

        # 2. Curated interaction pairs.
        med_names = [m.name.casefold() for m in meds]
        for rule in self._rules.interactions:
            if any(rule.drug_a.casefold() in name for name in med_names) and any(
                rule.drug_b.casefold() in name for name in med_names
            ):
                warnings.append(
                    f"Interaction: {rule.drug_a} + {rule.drug_b} — {rule.note}."
                )

        # 3. Dose thresholds. Unparseable doses yield no warning (known limit).
        for med in meds:
            med_name = med.name.casefold()
            for rule in self._rules.dose_limits:
                if rule.drug.casefold() not in med_name or not med.dose:
                    continue
                match = _DOSE_MG_RE.search(med.dose)
                if match and float(match.group(1)) > rule.max_single_dose_mg:
                    warnings.append(
                        f"Dose alert: {med.name} {med.dose} exceeds the "
                        f"{rule.max_single_dose_mg} mg single-dose threshold."
                    )

        return warnings
