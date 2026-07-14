"""Week 2 eval-driven CI gate.

Runs the golden set against the app with a STUBBED vision model (no live API),
scores each case with a boolean rubric, aggregates per category, and gates:
a category below its threshold — or, once a baseline is recorded, more than 5%
below it — fails the build (exit 1). This is the hard gate the graders probe by
injecting a regression.

  python -m evals.week2.runner                 # run + gate (exit code)
  python -m evals.week2.runner --update-baseline   # record current scores as baseline
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient

from app.documents import routes as doc_routes
from app.documents.extractor import VisionExtractor
from app.documents.ingest import InMemoryDocumentStore
from app.main import create_app

from . import checkers

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.yaml"
BASELINE = HERE / "baseline.json"
RESULTS = HERE / "RESULTS.md"
REGRESSION_TOLERANCE = 0.05


def _stub_extractor(draft: dict) -> VisionExtractor:
    async def create(**kwargs):
        return SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", name="record_lab_results", input=draft)
        ])
    return VisionExtractor(SimpleNamespace(messages=SimpleNamespace(create=create)))


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.text = ""

    def emit(self, record: logging.LogRecord) -> None:
        self.text += self.format(record) + "\n"


def _run_case(client: TestClient, app, case: dict, fixtures: dict, capture: _LogCapture) -> bool:
    """Execute one case and return True if its rubric category passes."""
    app.dependency_overrides[doc_routes.get_document_extractor] = lambda: _stub_extractor(
        fixtures[case["fixture"]]
    )
    capture.text = ""

    ingest = client.post(
        "/documents",
        files={"file": ("doc.pdf", b"%PDF-1.4 eval", "application/pdf")},
        data={"patient_id": case["patient_id"], "doc_type": "lab_pdf"},
        headers={"X-Clinician-Id": "eval-bot"},
    )

    category = case["category"]

    if case["kind"] == "ingest":
        if ingest.status_code != 200:
            return False
        body = ingest.json()
        if category == "schema_valid":
            return checkers.schema_valid(
                {"document_id": body["document_id"], "patient_id": body["patient_id"],
                 "results": body["results"]}
            )
        if category == "citation_present":
            return checkers.citation_present(body["results"])
        if category == "no_phi_in_logs":
            return checkers.no_phi_in_logs(capture.text, phi_values=case.get("phi_values", []))
        return False

    # kind == "ask": answerer must ground in the ingested facts (A+B) — red until built.
    ask = client.post(
        "/ask",
        json={"patient_id": case["patient_id"], "question": case["question"]},
        headers={"X-Clinician-Id": "eval-bot"},
    )
    if ask.status_code != 200:
        return False
    body = ask.json()
    if category == "citation_present":
        return checkers.citation_present(body.get("citations", []))
    if category == "factually_consistent":
        return checkers.factually_consistent(
            body.get("answer", ""), expected=case.get("must_contain", []),
            forbidden=case.get("must_not_contain", []),
        )
    if category == "safe_refusal":
        return checkers.safe_refusal(body.get("answer", ""), degraded=body.get("degraded", False))
    if category == "no_phi_in_logs":
        return checkers.no_phi_in_logs(capture.text, phi_values=case.get("phi_values", []))
    return False


def run() -> dict[str, dict]:
    spec = yaml.safe_load(CASES.read_text())
    fixtures, cases = spec["fixtures"], spec["cases"]

    capture = _LogCapture()
    capture.setFormatter(logging.Formatter("%(name)s %(message)s"))
    logging.getLogger().addHandler(capture)

    app = create_app()
    store = InMemoryDocumentStore()
    app.dependency_overrides[doc_routes.get_document_store] = lambda: store
    client = TestClient(app, raise_server_exceptions=False)

    per_category: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for case in cases:
        passed = _run_case(client, app, case, fixtures, capture)
        per_category[case["category"]].append((case["id"], passed))

    logging.getLogger().removeHandler(capture)
    return {
        cat: {
            "pass_rate": sum(p for _, p in rows) / len(rows),
            "cases": rows,
        }
        for cat, rows in per_category.items()
    }


def gate(scores: dict[str, dict], config: dict) -> tuple[bool, list[str]]:
    thresholds = config["thresholds"]
    baseline = config.get("baseline")
    failures: list[str] = []
    for category in checkers.CATEGORIES:
        rate = scores.get(category, {}).get("pass_rate", 0.0)
        threshold = thresholds.get(category, 1.0)
        if rate < threshold:
            failures.append(f"{category}: {rate:.0%} < threshold {threshold:.0%}")
        elif baseline and rate < baseline.get(category, 0.0) - REGRESSION_TOLERANCE:
            failures.append(
                f"{category}: {rate:.0%} regressed >5% vs baseline {baseline[category]:.0%}"
            )
    return (not failures), failures


def _write_results(scores: dict[str, dict], passed: bool, failures: list[str]) -> None:
    lines = [
        f"# Week 2 Eval Gate — {'PASS ✅' if passed else 'FAIL ❌'}",
        "",
        "| Category | Pass rate | Cases |",
        "|----------|-----------|-------|",
    ]
    for category in checkers.CATEGORIES:
        s = scores.get(category)
        if not s:
            continue
        detail = ", ".join(f"{cid}{'✓' if ok else '✗'}" for cid, ok in s["cases"])
        lines.append(f"| {category} | {s['pass_rate']:.0%} | {detail} |")
    if failures:
        lines += ["", "**Gate failures:**", *[f"- {f}" for f in failures]]
    RESULTS.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    scores = run()
    config = json.loads(BASELINE.read_text())

    if args.update_baseline:
        config["baseline"] = {c: round(scores.get(c, {}).get("pass_rate", 0.0), 4) for c in checkers.CATEGORIES}
        BASELINE.write_text(json.dumps(config, indent=2) + "\n")
        print("baseline updated:", config["baseline"])
        return 0

    passed, failures = gate(scores, config)
    _write_results(scores, passed, failures)
    for category in checkers.CATEGORIES:
        s = scores.get(category)
        if s:
            print(f"[{'PASS' if s['pass_rate'] >= config['thresholds'].get(category, 1.0) else 'FAIL'}] "
                  f"{category}: {s['pass_rate']:.0%}")
    if not passed:
        print("\nGATE FAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nGATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
