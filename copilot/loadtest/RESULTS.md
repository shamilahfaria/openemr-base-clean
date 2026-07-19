# Load Test Results — Clinical Co-Pilot

Target `https://copilot-early-sub.up.railway.app/health` · 200 requests per scenario.

| Concurrency | Requests | Throughput (rps) | Error % | p50 (ms) | p95 (ms) | p99 (ms) |
|-------------|----------|------------------|---------|----------|----------|----------|
| 10 | 200 | 189.6 | 0.0 | 42.8 | 78.9 | 213.1 |
| 50 | 200 | 260.3 | 0.0 | 169.9 | 208.4 | 220.2 |

`/health` is the infra baseline (no LLM tokens, no PHI). The `/chat` path
adds one bounded LLM call + FHIR reads on the same request stack — load-test
it directly with `--path /chat --token ... --patient ...`.
