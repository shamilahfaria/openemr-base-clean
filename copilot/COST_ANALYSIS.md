# AI Cost Analysis — Clinical Co-Pilot

Not `cost-per-token × N`. Cost is driven by **turns per active clinician per
shift**, the **tool-call fan-out per turn** (each tool call is another model
round-trip carrying the growing message history), and the **architectural
changes** each scale tier forces. Below: dev spend to date, a per-turn unit
cost, projected monthly cost at 4 user tiers, and what has to change at each.

## Model & unit economics

Model: **Claude Sonnet-class** (summarization + tool use; the verifier is
deterministic and adds **zero** token cost — a core cost decision). Assumed
list pricing ~**$3 / M input tokens**, ~**$15 / M output tokens**.

**Per-turn token profile** (measured shape from the smoke test + design):

| Component | Input tok | Output tok |
|-----------|-----------|-----------|
| System prompt + tool specs | ~700 | — |
| User message + prior turns | ~500 | — |
| 2 tool calls × (assistant tool_use + tool_result records) | ~2,500 | ~300 |
| Final synthesized answer | — | ~350 |
| **Per turn (≈3 model round-trips)** | **~3,700** | **~650** |

Per-turn cost ≈ (3,700 × $3 + 650 × $15) / 1e6 ≈ **$0.021/turn**. Prompt caching
of the static system+tool block (~700 tok) trims input ~15–20% at scale.

## Dev spend to date

Development to Early Submission used Claude via **Claude Code** (not billed
against the app's API budget) plus a handful of live `/chat` smoke turns.
Direct app-API dev spend: **< $1** (single-digit real turns; most iteration ran
against faked LLM responses in the 241-test suite and the deterministic eval
harness — a deliberate cost control).

## Usage model

One hospice RN ≈ **40 co-pilot turns / 12-hr shift** (start-of-shift review on
~5 patients + PRN/documentation lookups). ~**21 shifts/month**. So
**~840 turns / active clinician / month**.

## Projected monthly cost by scale

| Users (active clinicians) | Turns/mo | LLM cost/mo | Notes |
|---------------------------|----------|-------------|-------|
| **100** | 84K | **~$1.8K** | single sidecar instance; in-memory sessions fine |
| **1,000** | 840K | **~$18K** | +prompt caching (~-18% input); horizontal sidecar replicas |
| **10,000** | 8.4M | **~$150K** | caching + **batch/off-peak** pre-compute of start-of-shift summaries; per-tenant rate limits |
| **100,000** | 84M | **~$1.3M** | tiered models (Haiku for cheap retrieval turns, Sonnet for synthesis); negotiated/committed-use pricing; regional BAA endpoints |

(LLM cost only; infra below is a rounding error until ~10K.)

## Architectural changes forced at each tier — the real story

- **100 →** what we have. One FastAPI instance, in-memory `SessionStore`,
  Langfuse for cost/latency. No changes.
- **1,000 →** sessions must survive replica restarts and load-balancing:
  **swap in-memory `SessionStore` for Redis** (the documented scale-out). Turn
  on **prompt caching**. Add per-clinician rate limiting.
- **10,000 →** the expensive pattern is 5 patients × start-of-shift review at
  07:00 — a thundering herd. **Pre-compute patient summaries off-peak / on chart
  open** and serve turns from cache; this cuts tool-call round-trips (the
  dominant token cost) more than any price negotiation. Shard by facility/tenant.
- **100,000 →** **model tiering** (route cheap retrieval turns to Haiku, reserve
  Sonnet for synthesis) is the biggest lever — potentially halving blended
  cost. Committed-use discounts; multi-region BAA endpoints for data residency;
  the deterministic verifier and audit trail stay CPU-bound and scale linearly
  and cheaply.

## Cost controls already built in

- **Deterministic verification & rules** — the trust layer costs $0 in tokens
  (no LLM-judge).
- **Summary-first tool plan** — cheap orientation before deep retrieval bounds
  round-trips per turn.
- **`MAX_TOOL_ITERATIONS` cap** — bounds worst-case token spend per turn.
- **Faked-LLM test + eval harness** — 241 tests + 16 evals run at **zero** API
  cost, so correctness work doesn't burn budget.
