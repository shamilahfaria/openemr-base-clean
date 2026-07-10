"""Eval runner — executes cases.json against the REAL pipeline components.

Usage (from copilot/):  python -m evals.run
Writes evals/RESULTS.md and exits non-zero on any failure, so it can gate CI.

Executors use the real Verifier (with the committed clinical rules), the real
PatientScopeGuard, SessionStore, wiring fallback provider, and the real /chat
endpoint (orchestrator faked per case — evals measure OUR guarantees, not the
LLM's mood; live-LLM evals layer on top of this harness).
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import chat
from app.audit import AuditTrail
from app.main import create_app
from app.observability import TurnTelemetry
from app.orchestrator import TurnDraft
from app.scope import PatientScopeGuard, ScopeViolation
from app.sessions import SessionPatientMismatch, SessionStore
from app.tools.chart import AllergyRecord, GoalsOfCareRecord, MedicationRecord
from app.verifier import Verifier
from app.wiring import build_fallback_provider, load_clinical_rules

EVALS_DIR = Path(__file__).resolve().parent
RULES = load_clinical_rules(str(EVALS_DIR.parent / "rules" / "clinical_rules.json"))
PATIENT = "uuid-pat-1"
TOKEN = "eval-bearer-token"


def build_record(spec: dict):
    kind = spec["type"]
    if kind == "med":
        return MedicationRecord(
            source_id=spec["source_id"], name=spec["name"], dose=spec.get("dose"),
            route=None, sig=None, is_prn=False, prn_interval=None, status="active",
        )
    if kind == "allergy":
        return AllergyRecord(
            source_id=spec["source_id"], substance=spec["substance"],
            criticality=None, reactions=[],
        )
    if kind == "goals":
        return GoalsOfCareRecord(
            source_id=spec["source_id"], code=spec["code"],
            question=spec["question"], answer=spec["answer"], effective=None,
        )
    raise ValueError(f"unknown record type: {kind}")


def run_verifier_case(params: dict) -> dict:
    records = [build_record(s) for s in params.get("records", [])]
    draft = TurnDraft(answer=params["answer"], retrieved=records, tools_used=[])
    result = Verifier(RULES).verify(draft)
    return {
        "passed": result.passed,
        "answer": result.answer,
        "warnings": result.warnings,
        "withheld_count": len(result.withheld),
        "citation_ids": [c.source_id for c in result.citations],
    }


def run_scope_case(params: dict) -> dict:
    guard = PatientScopeGuard(params["active_patient"])
    try:
        guard.validate_tool_call(params["tool"], params["arguments"])
        return {"refused": False}
    except ScopeViolation:
        return {"refused": True}


def run_session_case(params: dict) -> dict:
    store = SessionStore()
    store.append("shared-session", "uuid-pat-1", "user", "question about patient one")
    try:
        store.history("shared-session", "uuid-pat-2")
        return {"refused": False}
    except SessionPatientMismatch:
        return {"refused": True}


def run_telemetry_case(params: dict) -> dict:
    return {"fields": list(TurnTelemetry.model_fields)}


def run_fallback_case(params: dict) -> dict:
    class FakeClient:
        async def get(self, path, *, bearer_token, params=None):
            return {
                "resourceType": "Bundle",
                "entry": [{"resource": r} for r in params_case_encounters],
            }

    params_case_encounters = params.get("encounters", [])
    fallback = build_fallback_provider(FakeClient())
    answer = asyncio.get_event_loop().run_until_complete(fallback(PATIENT, TOKEN))
    return {"answer": answer}


def run_endpoint_case(params: dict) -> dict:
    class FakeOrchestrator:
        async def run_turn(self, **kwargs):
            if params.get("orchestrator_error"):
                raise RuntimeError("simulated outage")
            records = [build_record(s) for s in params.get("records", [])]
            return TurnDraft(
                answer=params.get("draft_answer", ""), retrieved=records, tools_used=[]
            )

    async def fake_fallback(patient_id: str, bearer_token: str) -> str:
        return "Recent visit history: 2026-07-01 hospice admission."

    app = create_app()
    app.dependency_overrides[chat.get_orchestrator] = lambda: FakeOrchestrator()
    app.dependency_overrides[chat.get_verifier] = lambda: Verifier(RULES)
    app.dependency_overrides[chat.get_audit_trail] = lambda: AuditTrail()
    app.dependency_overrides[chat.get_fallback_provider] = lambda: fake_fallback
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/chat",
        json={
            "patient_id": PATIENT,
            "message": params.get("message", "what should I know?"),
            "session_id": "eval-session",
        },
        headers={"Authorization": f"Bearer {TOKEN}", "X-Clinician-Id": "eval-nurse"},
    )
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    return {
        "status_code": response.status_code,
        "degraded": body.get("degraded"),
        "answer": body.get("answer", ""),
        "warnings": body.get("warnings", []),
    }


EXECUTORS = {
    "verifier": run_verifier_case,
    "scope": run_scope_case,
    "session": run_session_case,
    "telemetry_model": run_telemetry_case,
    "fallback_provider": run_fallback_case,
    "endpoint": run_endpoint_case,
}


def check(expect: dict, actual: dict) -> list[str]:
    problems = []
    if "status_code" in expect and actual.get("status_code") != expect["status_code"]:
        problems.append(f"status_code {actual.get('status_code')} != {expect['status_code']}")
    if "degraded" in expect and actual.get("degraded") != expect["degraded"]:
        problems.append(f"degraded {actual.get('degraded')} != {expect['degraded']}")
    if "passed" in expect and actual.get("passed") != expect["passed"]:
        problems.append(f"passed {actual.get('passed')} != {expect['passed']}")
    if "refused" in expect and actual.get("refused") != expect["refused"]:
        problems.append(f"refused {actual.get('refused')} != {expect['refused']}")
    if "withheld_count" in expect and actual.get("withheld_count") != expect["withheld_count"]:
        problems.append(f"withheld_count {actual.get('withheld_count')} != {expect['withheld_count']}")
    if expect.get("answer_non_blank") and not actual.get("answer", "").strip():
        problems.append("answer is blank")
    for needle in expect.get("answer_includes", []):
        if needle not in actual.get("answer", ""):
            problems.append(f"answer missing: {needle!r}")
    for needle in expect.get("answer_excludes", []):
        if needle in actual.get("answer", ""):
            problems.append(f"answer leaked: {needle!r}")
    if "warnings_contain" in expect:
        if not any(expect["warnings_contain"] in w for w in actual.get("warnings", [])):
            problems.append(f"no warning containing {expect['warnings_contain']!r}")
    if "citations_include" in expect:
        if expect["citations_include"] not in actual.get("citation_ids", []):
            problems.append(f"citation missing: {expect['citations_include']!r}")
    for field in expect.get("fields_absent", []):
        if field in actual.get("fields", []):
            problems.append(f"forbidden field present: {field}")
    return problems


def main() -> int:
    dataset = json.loads((EVALS_DIR / "cases.json").read_text())
    rows, failures = [], 0

    for case in dataset["cases"]:
        try:
            actual = EXECUTORS[case["kind"]](case.get("params", {}))
            problems = check(case.get("expect", {}), actual)
        except Exception as exc:  # an executor crash is a failed case
            problems = [f"executor crashed: {type(exc).__name__}: {exc}"]
        status = "PASS" if not problems else "FAIL"
        failures += bool(problems)
        rows.append((case, status, problems))
        print(f"[{status}] {case['id']}" + (f" — {'; '.join(problems)}" if problems else ""))

    total = len(rows)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Eval Results — Clinical Co-Pilot",
        "",
        f"Dataset `{dataset['dataset_version']}` · rules `{RULES.version}` · run {now}",
        "",
        f"**{total - failures}/{total} passing.** Each case documents the failure mode it guards",
        "(assignment: boundaries, invariants, regression risks — no happy-path-only suites).",
        "",
        "| Case | Category | Result | Failure mode guarded |",
        "|------|----------|--------|----------------------|",
    ]
    for case, status, problems in rows:
        mark = "✅" if status == "PASS" else "❌ " + "; ".join(problems)
        lines.append(f"| {case['id']} | {case['category']} | {mark} | {case['failure_mode']} |")
    lines += [
        "",
        "Run with `python -m evals.run` from `copilot/` (non-zero exit on failure — CI-gateable).",
        "Cases execute the real verifier (committed clinical rules), scope guard, session store,",
        "fallback provider, and /chat endpoint; the LLM is faked per case so evals pin OUR",
        "guarantees deterministically. Live-LLM grading layers on top of this harness.",
        "",
    ]
    (EVALS_DIR / "RESULTS.md").write_text("\n".join(lines))
    print(f"\n{total - failures}/{total} passing → evals/RESULTS.md")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
