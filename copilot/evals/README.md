# Evals — the 6-Layer Maturity Model

Our eval strategy follows the **6-Layer Maturity Model for Production AI Quality**
("Evals That Actually Work"). The model's rule is *start at Layer 1 and add
layers as the system matures* — so this doc states honestly which layers we
have, which are partial, and which are deliberately deferred.

The point of the whole thing (Layer 0, really): **without evals you ship and
hope; with evals you ship and know.** The graders inject a regression — Layer 1
must catch it, and our CI gate blocks the build when it does.

| # | Layer | What it is | Our implementation | Status |
|---|-------|------------|--------------------|--------|
| 1 | **Golden Sets** | "What correct looks like" — small (10–20), fast; if these fail, something is fundamentally broken | [`week2/cases.yaml`](week2/cases.yaml) | ✅ |
| 2 | **Behavioral Coverage** | Coverage by category | 5 rubric categories over the golden set | ✅ |
| 3 | **Error Analysis** | Weekly review of real traces to sharpen categories | Needs production traces | ⬜ deferred |
| 4 | **Replay Harnesses** | ML-grade metrics: precision / recall / groundedness / faithfulness / tool-accuracy | Groundedness + faithfulness via citation/consistency checks; retrieval metrics land with hybrid RAG | ◐ partial |
| 5 | **Rubrics** | Multi-dimensional quality | **Boolean** rubrics (the assignment mandates "not 1–10") | ✅ boolean flavor |
| 6 | **Experiments** | Data-driven A/B decisions | Post-MVP | ⬜ deferred |

## Layer 1 — Golden Sets

[`week2/cases.yaml`](week2/cases.yaml) is our golden set. It mirrors the class's
`golden_data.yaml` shape — each case has an `id`, an input (`question` / upload
`fixture`), and expectations in the same vocabulary:

| class `golden_data.yaml` | our field |
|---|---|
| `must_contain` | `must_contain` |
| `must_not_contain` | `must_not_contain` |
| `expected_sources` | citation `source_id` (checked by `citation_present`) |
| `expected_tools` | routing decisions (Layer 4, with LangGraph) |

Small and fast by design: the MVP set is a handful of cases across every
category and grows toward the 50-case target for Early submission. If Layer 1
fails, the build stops — no higher layer runs.

## Layer 2 — Behavioral Coverage

Coverage is organized by **category**, not a single score. The five categories
([`week2/checkers.py`](week2/checkers.py)) match the assignment's required set:

- `schema_valid` — extraction validates against the strict schema (hallucinated
  fields rejected).
- `citation_present` — every clinical claim resolves to a `source_id`.
- `factually_consistent` — the answer contains the grounded facts and none of
  the forbidden ones.
- `safe_refusal` — missing-data questions degrade instead of inventing.
- `no_phi_in_logs` — extracted values/quotes never appear in logs or traces.

Each is a deterministic checker, so the whole gate runs in CI **with no live
API** (stubbed vision model).

## Layer 3 — Error Analysis *(deferred)*

Error analysis is a *weekly review of real traces* to discover which failure
modes actually matter — you can't do it before you have production traffic.
We have the substrate for it (correlation-ID traces, PHI-free telemetry, the
Langfuse pipeline), and it's the first layer we add once the Week 2 flow is
deployed and taking real (synthetic-demo) traffic.

## Layer 4 — Replay Harnesses *(partial)*

Layer 4 adds ML-grade metrics. We already assert **groundedness** (citation
presence + factual consistency) and **faithfulness** (safe refusal / no
invention). The retrieval metrics — precision, recall, tool-accuracy — arrive
with the hybrid RAG + rerank work in Early submission; that's when a replay
harness over the guideline corpus becomes meaningful.

## Layer 5 — Rubrics

The model's Layer 5 is multi-dimensional quality, classically via an LLM judge.
The assignment constrains this to **boolean** rubrics ("not 1–10"), which is what
we implement — deterministic where possible. `factually_consistent` is a
deterministic proxy today; its signature is a drop-in slot for a **pinned LLM
judge** (fixed yes/no rubric, pinned config) without touching the runner. "Use
it right, or don't use it": a judge only goes in behind a frozen rubric.

## Layer 6 — Experiments *(deferred)*

A/B comparison of configs (models, prompts, retrieval params) against the golden
set. Deferred until the system is stable enough that config choices are the main
lever — post-MVP.

## The gate — "catch regressions before shipping"

[`week2/runner.py`](week2/runner.py) runs the golden set, scores each rubric
category, and **blocks the build** (exit 1) if any category falls below its
threshold or regresses more than 5% below the committed baseline
([`week2/baseline.json`](week2/baseline.json)). Regressions are diffs, not vibes.

```bash
python -m evals.week2.runner              # run + gate (CI + pre-push use this)
python -m evals.week2.runner --update-baseline   # record new baseline when green
```

Wired two ways (PRD FR-6.3): the [`.githooks/pre-push`](../../.githooks/pre-push)
hook (`git config core.hooksPath .githooks`) and the GitLab pipeline
([`.gitlab-ci.yml`](../../.gitlab-ci.yml)) — both fail closed on a regression.

The git history shows the intended red→green cycle: the gate was committed
**red** (answer categories failing), then the answerer turned it **green** — the
gate was never retrofitted to pass.

---

The Week 1 deterministic suite ([`run.py`](run.py)) and live-LLM eval
([`live.py`](live.py)) remain the baseline for the chart-Q&A agent; this doc
covers the Week 2 document-ingestion gate.
