"""Live-LLM eval assertion logic.

Unit-tests the pure ``check_turn`` verdict (no network, no model): a live turn
passes only when it is verified (not degraded), cited, and — when a topical
hint is given — on-topic.
"""
from __future__ import annotations

from evals.live import check_turn

CASE = {
    "name": "code-status",
    "patient": "p",
    "message": "code status?",
    "expect_any": ["resuscitat", "dnr"],
    "require_citations": True,
}


def test_verified_cited_on_topic_turn_passes():
    body = {
        "degraded": False,
        "answer": "The patient is DNR (Do Not Attempt Resuscitation).",
        "citations": [{"claim": "DNR", "source_id": "goc-1"}],
    }
    passed, reasons = check_turn(body, CASE)
    assert passed is True
    assert reasons == []


def test_fallback_turn_fails():
    body = {"degraded": True, "answer": "Recent visit history: ...", "citations": []}
    passed, reasons = check_turn(body, CASE)
    assert passed is False
    assert any("degraded" in r for r in reasons)


def test_uncited_turn_fails():
    body = {"degraded": False, "answer": "The patient is DNR.", "citations": []}
    passed, reasons = check_turn(body, CASE)
    assert passed is False
    assert any("citation" in r.lower() for r in reasons)


def test_off_topic_answer_fails_the_topical_hint():
    body = {
        "degraded": False,
        "answer": "The patient is comfortable this morning.",
        "citations": [{"claim": "x", "source_id": "s1"}],
    }
    passed, reasons = check_turn(body, CASE)
    assert passed is False
    assert any("matched none" in r for r in reasons)


def test_citations_not_required_when_case_opts_out():
    body = {"degraded": False, "answer": "General guidance.", "citations": []}
    case = {"name": "n", "patient": "p", "message": "m", "require_citations": False}
    passed, reasons = check_turn(body, case)
    assert passed is True
