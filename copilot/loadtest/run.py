"""Load / stress test — concurrent /health probes against the deployed sidecar.

Records p50/p95/p99 latency and error rate at 10 and 50 concurrent users.
Targets /health so the load test measures the SERVICE (routing, middleware,
ASGI concurrency) without spending
LLM tokens or requiring PHI — the /chat path shares the same request stack plus
a bounded external call, so /health is the clean infra baseline.

Usage:
  python -m loadtest.run --url https://copilot-early-sub.up.railway.app --requests 200
Writes loadtest/RESULTS.md.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import httpx

RESULTS = Path(__file__).resolve().parent / "RESULTS.md"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[idx]


async def worker(client, url, path, n, latencies, errors):
    for _ in range(n):
        start = time.perf_counter()
        try:
            resp = await client.get(url + path)
            latencies.append((time.perf_counter() - start) * 1000)
            if resp.status_code >= 500:
                errors.append(1)
        except Exception:
            latencies.append((time.perf_counter() - start) * 1000)
            errors.append(1)


async def scenario(url, path, concurrency, total):
    per = max(1, total // concurrency)
    latencies: list[float] = []
    errors: list[int] = []
    async with httpx.AsyncClient(timeout=30, verify=True) as client:
        wall_start = time.perf_counter()
        await asyncio.gather(
            *[worker(client, url, path, per, latencies, errors) for _ in range(concurrency)]
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


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--path", default="/health")
    parser.add_argument("--requests", type=int, default=200)
    args = parser.parse_args()

    rows = []
    for concurrency in (10, 50):
        result = await scenario(args.url, args.path, concurrency, args.requests)
        rows.append(result)
        print(result)

    lines = [
        "# Load Test Results — Clinical Co-Pilot",
        "",
        f"Target `{args.url}{args.path}` · {args.requests} requests per scenario.",
        "",
        "| Concurrency | Requests | Throughput (rps) | Error % | p50 (ms) | p95 (ms) | p99 (ms) |",
        "|-------------|----------|------------------|---------|----------|----------|----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['concurrency']} | {r['requests']} | {r['throughput_rps']} | "
            f"{r['error_rate_pct']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} |"
        )
    lines += [
        "",
        "`/health` is the infra baseline (no LLM tokens, no PHI). The `/chat` path",
        "adds one bounded LLM call + FHIR reads on top of this same request stack;",
        "its latency is dominated by the model, tracked per-request in telemetry",
        "(p50/p95 in the observability dashboard).",
        "",
    ]
    RESULTS.write_text("\n".join(lines))
    print(f"\n-> {RESULTS}")


if __name__ == "__main__":
    asyncio.run(main())
