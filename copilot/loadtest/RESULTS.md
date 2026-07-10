# Load Test Results — Clinical Co-Pilot

Target `http://localhost:8055/health` · 500 requests per scenario.

| Concurrency | Requests | Throughput (rps) | Error % | p50 (ms) | p95 (ms) | p99 (ms) |
|-------------|----------|------------------|---------|----------|----------|----------|
| 10 | 500 | 525.8 | 0.0 | 13.0 | 42.4 | 99.1 |
| 50 | 500 | 360.0 | 0.0 | 93.8 | 296.3 | 476.6 |

`/health` is the infra baseline (no LLM tokens, no PHI). The `/chat` path
adds one bounded LLM call + FHIR reads on top of this same request stack;
its latency is dominated by the model, tracked per-request in telemetry
(p50/p95 in the observability dashboard).
