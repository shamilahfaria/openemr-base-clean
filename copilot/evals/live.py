"""Live-LLM eval — real turns against the deployed agent.

The deterministic suite (``evals/run.py``) fakes the model, so it cannot catch
the one failure that actually bit us in production: the model occasionally not
calling the right tool, so a real turn silently degrades to the visit-history
fallback. This suite runs REAL ``/chat`` turns against seeded patients and
asserts each came back verified (``degraded=false``) with citations — the check
the faked suite structurally cannot make.

Requires a running sidecar (with its own ``ANTHROPIC_API_KEY`` + OpenEMR) and a
valid OAuth bearer for that OpenEMR. It spends a few bounded LLM turns, so it is
run on demand, not in CI.

Usage:
  python -m evals.live --url https://copilot-early-sub.up.railway.app \\
      --token "$BEARER" --clinician nurse-maria
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
CASES = HERE / "live_cases.json"
RESULTS = HERE / "LIVE_RESULTS.md"


def check_turn(body: dict, case: dict) -> tuple[bool, list[str]]:
    """Assert a real turn is verified, cited, and (optionally) on-topic.

    Pure so it is unit-testable without a network or the model.
    """
    reasons: list[str] = []
    if body.get("degraded") is not False:
        reasons.append("degraded/fallback (expected a verified turn)")
    citations = body.get("citations") or []
    if case.get("require_citations", True) and not citations:
        reasons.append("no citations (expected the answer to cite the record)")
    answer = (body.get("answer") or "").lower()
    expect_any = [s.lower() for s in case.get("expect_any", [])]
    if expect_any and not any(term in answer for term in expect_any):
        reasons.append(f"answer matched none of {case['expect_any']}")
    return (not reasons), reasons


async def run_case(client: httpx.AsyncClient, url: str, token: str, clinician: str, case: dict) -> dict:
    resp = await client.post(
        url + "/chat",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Clinician-Id": clinician,
            "Content-Type": "application/json",
        },
        json={
            "patient_id": case["patient"],
            "message": case["message"],
            "session_id": f"live-{uuid.uuid4().hex[:12]}",
        },
    )
    if resp.status_code != 200:
        return {"name": case["name"], "passed": False, "reasons": [f"HTTP {resp.status_code}"]}
    body = resp.json()
    passed, reasons = check_turn(body, case)
    return {
        "name": case["name"],
        "passed": passed,
        "reasons": reasons,
        "degraded": body.get("degraded"),
        "citations": len(body.get("citations") or []),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--clinician", default="nurse-maria")
    parser.add_argument("--cases", default=str(CASES))
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text())["cases"]
    results = []
    async with httpx.AsyncClient(timeout=60, verify=True) as client:
        for case in cases:
            result = await run_case(client, args.url, args.token, args.clinician, case)
            results.append(result)
            mark = "[PASS] " if result["passed"] else "[FAIL] "
            suffix = "" if result["passed"] else " — " + "; ".join(result["reasons"])
            print(mark + result["name"] + suffix)

    passed = sum(1 for r in results if r["passed"])
    lines = [
        f"# Live-LLM Eval — {passed}/{len(results)} verified",
        "",
        f"Target `{args.url}` · real turns; each asserts `degraded=false` + citations.",
        "",
        "| Case | Result | degraded | citations | notes |",
        "|------|--------|----------|-----------|-------|",
    ]
    for r in results:
        lines.append(
            f"| {r['name']} | {'✅' if r['passed'] else '❌'} | {r.get('degraded', '-')} | "
            f"{r.get('citations', '-')} | {'; '.join(r['reasons']) or '-'} |"
        )
    RESULTS.write_text("\n".join(lines) + "\n")
    print(f"\n{passed}/{len(results)} verified -> {RESULTS}")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
