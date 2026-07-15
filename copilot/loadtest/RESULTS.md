# Load Test Results — Clinical Co-Pilot

Target `https://copilot-early-sub.up.railway.app/health` · 200 requests per scenario.

| Concurrency | Requests | Throughput (rps) | Error % | p50 (ms) | p95 (ms) | p99 (ms) |
|-------------|----------|------------------|---------|----------|----------|----------|
| 10 | 200 | 194.4 | 0.0 | 42.9 | 64.3 | 185.6 |
| 50 | 200 | 228.2 | 0.0 | 191.3 | 231.8 | 247.7 |

`/health` is the infra baseline (no LLM tokens, no PHI). The `/chat` path
adds one bounded LLM call + FHIR reads on the same request stack — load-test
it directly with `--path /chat --token ... --patient ...`.
