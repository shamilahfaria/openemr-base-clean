"""Load / stress test for the deployed sidecar.

Two targets:

* ``/health`` (default) — the infra baseline: routing, middleware, ASGI
  concurrency, with no LLM tokens and no PHI.
* ``/chat`` — the real agent path (POST + OAuth bearer). Each request is one
  bounded LLM turn against a seeded patient, so it is **cost-aware by
  construction**: a fresh ``session_id`` per request (single-turn, no history
  growth) and small defaults (20 requests over 2/5 concurrency). Raise them
  deliberately with ``--requests`` / ``--concurrency``.

Both record p50/p95/p99 latency, throughput, and error rate per concurrency
level. Results write to ``RESULTS.md`` (health) or ``RESULTS-chat.md`` (chat).

Usage:
  python -m loadtest.run --url https://copilot-early-sub.up.railway.app --requests 200
  python -m loadtest.run --url https://copilot-early-sub.up.railway.app --path /chat \\
      --token "$BEARER" --patient a2390997-1e8c-4c41-99f5-676ad433d365
"""
from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "RESULTS.md"
DEFAULT_MESSAGE = "What is this patient's code status?"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[idx]


def chat_headers(token: str, clinician: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Clinician-Id": clinician,
        "Content-Type": "application/json",
    }


async def _issue(client: httpx.AsyncClient, url: str, path: str, chat_ctx: dict | None):
    """One request: POST /chat (fresh session) when chat_ctx is set, else GET."""
    if chat_ctx is not None:
        body = {
            "patient_id": chat_ctx["patient"],
            "message": chat_ctx["message"],
            "session_id": f"load-{uuid.uuid4().hex[:12]}",
        }
        return await client.post(url + path, headers=chat_ctx["headers"], json=body)
    return await client.get(url + path)


async def worker(client, url, path, n, latencies, errors, chat_ctx, error_status):
    for _ in range(n):
        start = time.perf_counter()
        try:
            resp = await _issue(client, url, path, chat_ctx)
            latencies.append((time.perf_counter() - start) * 1000)
            if resp.status_code >= error_status:
                errors.append(1)
        except Exception:
            latencies.append((time.perf_counter() - start) * 1000)
            errors.append(1)


async def scenario(
    url, path, concurrency, total, *, chat_ctx=None, error_status=500, transport=None
):
    per = max(1, total // concurrency)
    latencies: list[float] = []
    errors: list[int] = []
    async with httpx.AsyncClient(timeout=60, verify=True, transport=transport) as client:
        wall_start = time.perf_counter()
        await asyncio.gather(
            *[
                worker(client, url, path, per, latencies, errors, chat_ctx, error_status)
                for _ in range(concurrency)
            ]
        )
        wall = time.perf_counter() - wall_start
    count = len(latencies)
    return {
        "concurrency": concurrency,
        "requests": count,
        "throughput_rps": round(count / wall, 1) if wall else 0.0,
        "error_rate_pct": round(100 * len(errors) / count, 2) if count else 0.0,
        "p50_ms": round(percentile(latencies, 50), 1),
        "p95_ms": round(percentile(latencies, 95), 1),
        "p99_ms": round(percentile(latencies, 99), 1),
    }


def _table(rows: list[dict]) -> list[str]:
    lines = [
        "| Concurrency | Requests | Throughput (rps) | Error % | p50 (ms) | p95 (ms) | p99 (ms) |",
        "|-------------|----------|------------------|---------|----------|----------|----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['concurrency']} | {r['requests']} | {r['throughput_rps']} | "
            f"{r['error_rate_pct']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} |"
        )
    return lines


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--path", default="/health")
    parser.add_argument("--requests", type=int, default=None, help="per scenario")
    parser.add_argument("--concurrency", default=None, help="comma-separated levels")
    parser.add_argument("--token", default="", help="OAuth bearer (required for /chat)")
    parser.add_argument("--clinician", default="nurse-maria")
    parser.add_argument("--patient", default="", help="patient uuid (required for /chat)")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    is_chat = args.path.rstrip("/") == "/chat"
    if is_chat and not (args.token and args.patient):
        parser.error("--token and --patient are required for a /chat load test")

    total = args.requests if args.requests is not None else (20 if is_chat else 200)
    levels = [int(c) for c in (args.concurrency.split(",") if args.concurrency
                               else (["2", "5"] if is_chat else ["10", "50"]))]

    chat_ctx = None
    error_status = 500
    if is_chat:
        # Every non-2xx /chat is a failed turn (auth/session/agent), not just 5xx.
        error_status = 400
        chat_ctx = {
            "patient": args.patient,
            "message": args.message,
            "headers": chat_headers(args.token, args.clinician),
        }
        print(f"⚠ /chat mode issues real LLM turns: up to {total * len(levels)} total. "
              "Bounded by --requests/--concurrency.")

    rows = []
    for concurrency in levels:
        result = await scenario(
            args.url, args.path, concurrency, total,
            chat_ctx=chat_ctx, error_status=error_status,
        )
        rows.append(result)
        print(result)

    out = Path(args.out) if args.out else (HERE / "RESULTS-chat.md" if is_chat else RESULTS)
    lines = [
        "# Load Test Results — Clinical Co-Pilot",
        "",
        f"Target `{args.url}{args.path}` · {total} requests per scenario.",
        "",
        *_table(rows),
        "",
    ]
    if is_chat:
        lines += [
            "The `/chat` path is one bounded LLM turn per request (fresh session, no",
            "history growth). Latency is dominated by the model + FHIR reads; error %",
            "counts any non-2xx turn (auth/session/agent). Per-turn token cost is",
            "captured in telemetry and reconciled in COST_ANALYSIS.md.",
        ]
    else:
        lines += [
            "`/health` is the infra baseline (no LLM tokens, no PHI). The `/chat` path",
            "adds one bounded LLM call + FHIR reads on the same request stack — load-test",
            "it directly with `--path /chat --token ... --patient ...`.",
        ]
    out.write_text("\n".join(lines) + "\n")
    print(f"\n-> {out}")


if __name__ == "__main__":
    asyncio.run(main())
