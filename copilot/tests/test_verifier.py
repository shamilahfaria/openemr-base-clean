"""
TDD (Red) suite — build step 7: the deterministic verification layer.

Pins the two-check contract: source attribution (uncited/unknown -> withheld,
[general] -> labeled, markers stripped, citations structured) and clinical
rule checks (allergy cross-check, interactions, dose limits — flag, don't
block). Uses the real record models so the verifier exercises the same
contracts the tools produce.
"""
from __future__ import annotations

import pytest

from app.orchestrator import TurnDraft
from app.tools.chart import AllergyRecord, MedicationRecord
from app.verifier import ClinicalRuleSet, VerificationResult, Verifier

RULES = ClinicalRuleSet(
    version="2026.07.0",
    interactions=[
        {"drug_a": "morphine", "drug_b": "lorazepam", "note": "additive sedation"}
    ],
    dose_limits=[{"drug": "morphine", "max_single_dose_mg": 30.0}],
)


def med(source_id: str, name: str, dose: str | None = None) -> MedicationRecord:
    return MedicationRecord(
        source_id=source_id, name=name, dose=dose, route=None, sig=None,
        is_prn=False, prn_interval=None, status="active",
    )


def allergy(source_id: str, substance: str) -> AllergyRecord:
    return AllergyRecord(
        source_id=source_id, substance=substance, criticality=None, reactions=[]
    )


def draft(answer: str, retrieved: list | None = None) -> TurnDraft:
    return TurnDraft(answer=answer, retrieved=retrieved or [], tools_used=[])


def verify(answer: str, retrieved: list | None = None) -> VerificationResult:
    return Verifier(RULES).verify(draft(answer, retrieved))


MORPHINE = med("med-1", "Morphine sulfate", "5 mg")


class TestSourceAttribution:
    def test_fully_cited_answer_survives_with_structured_citations(self):
        result = verify("She is on morphine 5 mg [src: med-1].", [MORPHINE])
        assert result.passed
        assert "morphine 5 mg" in result.answer
        assert len(result.citations) == 1
        assert result.citations[0].source_id == "med-1"
        assert "morphine" in result.citations[0].claim

    def test_citation_markers_are_stripped_from_the_answer(self):
        result = verify("She is on morphine 5 mg [src: med-1].", [MORPHINE])
        assert "[src:" not in result.answer

    def test_unknown_source_id_withholds_the_sentence(self):
        result = verify("She is on fentanyl [src: med-99].", [MORPHINE])
        assert not result.passed
        assert "fentanyl" not in result.answer
        assert any("fentanyl" in w for w in result.withheld)

    def test_uncited_sentence_is_withheld(self):
        result = verify(
            "She is on morphine [src: med-1]. Her prognosis is weeks to months.",
            [MORPHINE],
        )
        assert "morphine" in result.answer
        assert "prognosis" not in result.answer
        assert any("prognosis" in w for w in result.withheld)

    def test_withholding_adds_a_warning(self):
        result = verify("Uncited claim here.", [MORPHINE])
        assert result.warnings  # the nurse must see that content was withheld

    def test_general_sentence_is_kept_and_labeled(self):
        result = verify(
            "Morphine can cause drowsiness [general]. She takes 5 mg [src: med-1].",
            [MORPHINE],
        )
        assert "drowsiness" in result.answer
        assert "(not from the patient record)" in result.answer
        assert "[general]" not in result.answer

    def test_general_sentences_produce_no_citations(self):
        result = verify("Morphine can cause drowsiness [general].", [MORPHINE])
        assert result.citations == []
        assert result.passed  # labeled outside-record content may stand alone

    def test_sentence_with_multiple_citations_yields_multiple_citation_entries(self):
        lorazepam = med("med-2", "Lorazepam", "0.5 mg")
        result = verify(
            "She takes morphine [src: med-1] and lorazepam [src: med-2].",
            [MORPHINE, lorazepam],
        )
        assert {c.source_id for c in result.citations} == {"med-1", "med-2"}

    def test_all_claims_withheld_fails_verification(self):
        result = verify(
            "Something uncited. Another thing [src: unknown-1].", [MORPHINE]
        )
        assert not result.passed
        assert result.answer.strip() == ""
        assert len(result.withheld) == 2

    def test_empty_draft_fails_verification(self):
        result = verify("", [MORPHINE])
        assert not result.passed

    def test_withheld_claims_never_appear_in_the_answer(self):
        result = verify(
            "Kept claim [src: med-1]. Withheld claim about dialysis.", [MORPHINE]
        )
        assert "dialysis" not in result.answer
        assert any("dialysis" in w for w in result.withheld)


class TestClinicalRules:
    def test_med_matching_patient_allergy_raises_a_warning(self):
        records = [med("med-3", "Penicillin V potassium"), allergy("alg-1", "penicillin")]
        result = verify("On penicillin [src: med-3].", records)
        assert result.passed  # flagged, not blocked
        assert any("penicillin" in w.lower() for w in result.warnings)

    def test_allergy_match_is_case_insensitive(self):
        records = [med("med-3", "PENICILLIN"), allergy("alg-1", "Penicillin")]
        result = verify("On penicillin [src: med-3].", records)
        assert any("penicillin" in w.lower() for w in result.warnings)

    def test_no_allergy_conflict_no_warning(self):
        records = [MORPHINE, allergy("alg-1", "penicillin")]
        result = verify("On morphine [src: med-1].", records)
        assert result.warnings == []

    def test_interaction_pair_in_retrieved_meds_raises_a_warning(self):
        records = [MORPHINE, med("med-2", "Lorazepam", "0.5 mg")]
        result = verify(
            "On morphine [src: med-1] and lorazepam [src: med-2].", records
        )
        assert any("sedation" in w for w in result.warnings)

    def test_interaction_with_only_one_drug_present_is_silent(self):
        result = verify("On morphine [src: med-1].", [MORPHINE])
        assert result.warnings == []

    def test_dose_over_limit_raises_a_warning(self):
        heavy = med("med-4", "Morphine sulfate", "40 mg")
        result = verify("Morphine 40 mg ordered [src: med-4].", [heavy])
        assert any("40" in w or "dose" in w.lower() for w in result.warnings)

    def test_dose_within_limit_is_silent(self):
        result = verify("Morphine 5 mg [src: med-1].", [MORPHINE])
        assert result.warnings == []

    def test_unparseable_dose_does_not_crash_or_warn(self):
        odd = med("med-5", "Morphine sulfate", "one tablet")
        result = verify("Morphine ordered [src: med-5].", [odd])
        assert result.passed
        assert result.warnings == []

    def test_rule_violations_do_not_block_the_answer(self):
        records = [med("med-3", "Penicillin"), allergy("alg-1", "penicillin")]
        result = verify("On penicillin [src: med-3].", records)
        assert result.passed
        assert "penicillin" in result.answer.lower()


class TestResultMetadata:
    def test_rules_version_is_surfaced(self):
        result = verify("Anything [src: med-1].", [MORPHINE])
        assert result.rules_version == "2026.07.0"

    def test_verification_is_deterministic(self):
        args = ("On morphine [src: med-1]. Uncited claim.", [MORPHINE])
        assert verify(*args) == verify(*args)
