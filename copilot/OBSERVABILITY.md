# Observability — Clinical Co-Pilot

You cannot improve what you cannot see. This service is observable three ways,
all keyed by the same **correlation ID** (minted or propagated per request by
`app/middleware.py`, echoed as `X-Correlation-ID`, present in the chat UI, the
audit trail, and every log line for the turn):

1. **Structured logs (stdout).** Every completed turn emits one
   `turn_telemetry {...json...}` INFO line (`app/observability.py`,
   `LoggingExporter`) carrying: correlation id, outcome
   (`verified`/`fallback`/`denied`), tools used, verification pass/fail,
   warning + withheld counts, latency, model, input/output tokens, and cost.
   On Railway: `railway logs`. A full trace of any request is reconstructable
   from logs alone via its correlation id.
2. **Live dashboard.** `GET /dashboard` renders `GET /metrics` — a PHI-free
   in-process aggregate (`app/metrics.py`): request count, error count and
   rate, p50/p95/p99 latency, verification pass rate, withheld-claim and
   warning totals, per-tool call counts, token and dollar totals, cost per
   turn, and the last 25 turns. Live at
   `https://copilot-early-sub.up.railway.app/dashboard`.
3. **HIPAA audit trail.** PHI-bearing disclosure records (which clinician saw
   what for which patient) stay inside the trust boundary in `app/audit.py` —
   deliberately separate from telemetry, which is PHI-free by construction
   (no patient id, no message text; enforced by eval I9-telemetry-phi-free).

The dashboard answers the assignment's four minimum questions: what did the
agent do on a request (recent-turns row + log trace by correlation id), how
long did each step take (latency percentiles + per-turn latency), did tools
fail (fallback/denied outcomes, tool counts vs. turns), and how many tokens at
what cost (live totals and per-turn unit cost, measured from API usage — the
same numbers that feed `COST_ANALYSIS.md`).

`/metrics` state is per-process and resets on deploy. That is an accepted
limitation at this scale (single Railway instance); the structured log stream
is the durable record, and a Langfuse exporter drops in behind the same
`TelemetryExporter` seam when multi-instance aggregation is needed.

## Alert definitions

Three alerts are defined over the `/metrics` aggregates. At current scale they
are evaluated by inspection of the dashboard (and are CI-checkable against
`/metrics` with `curl` + `jq`); the thresholds are the contract and move to a
metrics backend unchanged.

| Alert | Condition | What it means | On-call response |
|---|---|---|---|
| **High latency** | `latency_ms.p95 > 10000` (10 s) over the current window | Verified answers are arriving slower than the USER.md workstation budget (p95 ~8–10 s); the nurse will stop waiting. Usually upstream: OpenEMR FHIR latency or Anthropic API degradation. | Check `/ready` (which dependency is slow/unreachable); check `railway logs` for slow-turn correlation ids and tool mix; if the model is degraded, confirm fallback turns still serve. No restart needed for upstream latency — communicate and monitor. |
| **Elevated error rate** | `error_rate > 0.05` (5% of /chat responses are 4xx/5xx) | Users are being rejected (expired tokens → 401 bursts) or the service is failing (5xx). A 401 spike during a shift usually means OAuth token expiry, not an outage. | Split by class in `responses_by_class`: mostly 4xx → auth/session issue, verify token flow; any 5xx → pull correlation ids from logs, check `/ready`, restart the service if the process is wedged. |
| **Verification/tool failure** | `turn_outcomes.fallback / turns_total > 0.2`, or any `denied` turn | More than 1 in 5 answers is degrading to the visit-history fallback (verifier withholding heavily or tools failing), or a turn found agent AND fallback unavailable (`denied` — the fail-closed path). | Inspect recent fallback rows on the dashboard for their tool lists; grep logs by correlation id for `tool failed` / verifier withheld counts. Rules-file or FHIR-schema drift is the usual cause. Any `denied` turn is a page — both paths down means OpenEMR connectivity is gone; check `/ready` and the OpenEMR service first. |

## Answering "what happened on request X?"

```bash
railway logs | grep <correlation-id>
```

yields the request line, any agent/tool failure lines, and the
`turn_telemetry` record; the same id appears in the UI footer of the answer
card, on the `X-Correlation-ID` response header, in `/metrics` recent turns,
and in the audit trail entry recording the PHI disclosure.
