# Load Test Results — Clinical Co-Pilot

Target `https://copilot-early-sub.up.railway.app/chat` · 50 requests per scenario.

| Concurrency | Requests | Throughput (rps) | Error % | p50 (ms) | p95 (ms) | p99 (ms) |
|-------------|----------|------------------|---------|----------|----------|----------|
| 10 | 50 | 1.8 | 0.0 | 4230.3 | 6145.6 | 7617.5 |
| 50 | 50 | 5.8 | 0.0 | 4613.5 | 6462.1 | 8581.4 |

The `/chat` path is one bounded LLM turn per request (fresh session, no
history growth). Latency is dominated by the model + FHIR reads; error %
counts any non-2xx turn (auth/session/agent). Per-turn token cost is
captured in telemetry and reconciled in COST_ANALYSIS.md.
