# Clinical Co-Pilot — Presentation Walkthrough

A guided tour of the deployed system: what it does, how it's built, and where
to see each graded behaviour live. Written to be followed top-to-bottom in
about five minutes; every step runs against the real deployment.

**Live endpoints**

| Surface | URL |
|---------|-----|
| OpenEMR (with embedded Co-Pilot) | https://openemr-early-sub.up.railway.app |
| Co-Pilot chat panel | https://copilot-early-sub.up.railway.app/ui |
| Documents + ask panel | https://copilot-early-sub.up.railway.app/ui/documents |
| Observability dashboard | https://copilot-early-sub.up.railway.app/dashboard |

Login credentials are provided with the submission. All patient data is
synthetic (Synthea); no real PHI exists anywhere in the system.

---

## 1. The Co-Pilot lives inside the chart

Log into OpenEMR → Finder → search **Legros** → open **Brendon298 Legros616**.
A floating **Co-Pilot** button sits at the bottom-right of the patient
dashboard. Clicking it opens the Co-Pilot as a modal over the chart with the
active patient's FHIR uuid already wired in — no copy-pasting identifiers, no
separate app to visit.

This is delivered as an OpenEMR-side module (`library/copilot.php` +
`library/copilot_launcher.php`), injected into the stock patient-summary page
by the deployment image with build-time `php -l` guards, and framed under a
`frame-ancestors` CSP that only trusts the OpenEMR origins.

## 2. Grounded chat with deterministic verification

In the modal, click **⚡ Generate demo token** (visible to the demo admin
only), then ask: *"Code status and goals of care"*.

What to notice in the answer card:

- **"Verified against the record"** — every clinical claim was checked by a
  deterministic verifier against the FHIR records actually retrieved this
  turn. Unverifiable statements are withheld, not softened.
- **Sources** — each claim resolves to the record id it came from.
- Ask about a patient with no data (or a question the record can't answer)
  and the turn **degrades into a labeled fallback** instead of inventing.

## 3. Multimodal ingestion: two document types, strict schemas

Click **documents ↗** in the panel header (patient and clinician carry over).
Upload a lab report PDF with doc type *Lab report*, then an intake form with
doc type *Intake form*.

What to notice in the extraction table:

- Every extracted fact carries a **verbatim quote, page, and a citation
  anchored to the stored document id** — the model never supplies its own
  provenance; the ingestion pipeline stamps it.
- Extraction is validated against a **strict schema (`extra="forbid"`)**: a
  hallucinated field fails validation rather than entering the record.
- Unreadable fields stay **visible as low-confidence nulls** — shown as "—",
  never invented.
- Re-uploading the same file is **idempotent** (same document id, no
  duplicates).

## 4. Multi-agent graph + hybrid RAG

Still in the documents panel, ask: *"What changed in this patient's labs?"*

Expand the three disclosure sections under the answer:

- **Citations** — patient facts cite `lab_pdf`/`intake_form` documents;
  guideline evidence cites `guideline` chunks. Separated, never blended.
- **Guideline evidence — hybrid retrieval** — each hit shows its **keyword
  (BM25) rank, dense (embedding cosine) rank, and final rerank score**. The
  channels are fused with reciprocal-rank fusion and reranked by query-term
  coverage; retrieval is deterministic and inspectable end to end.
- **Supervisor routing** — the LangGraph supervisor's actual decisions:
  `intake → evidence → answer`, each with its logged reason. Handoffs are
  data, not vibes.

## 5. The eval-driven CI hard gate

Behaviour is protected by a **50-case golden set** scored on five boolean
rubrics — `schema_valid`, `citation_present`, `factually_consistent`,
`safe_refusal`, `no_phi_in_logs` — gated per category with a committed
baseline (any category below threshold, or regressing >5% vs baseline, fails
the build). It runs with a stubbed vision model: fully offline, deterministic,
no API keys.

```bash
cd copilot
python -m evals.week2.runner            # -> GATE PASSED (all 5 categories 100%)

# The grader scenario — inject a grounding regression, watch it block:
perl -0pi -e 's/return \{"facts": facts, "extracted": True\}/return {"facts": [], "extracted": True}/' app/graph/nodes.py
python -m evals.week2.runner            # -> GATE FAILED (exit 1): citation_present 50%, factually_consistent 0%
git checkout -- app/graph/nodes.py
python -m evals.week2.runner            # -> GATE PASSED again
```

The same command sequence runs in CI (`.gitlab-ci.yml`) alongside the 327-test
pytest suite and the 16-case Week 1 eval set, and a pre-push hook runs the
gate locally before code leaves the machine.

---

## Architecture at a glance

```
OpenEMR (patient chart)
  └─ Co-Pilot modal (library/copilot_launcher.php, CSP-framed iframe)
       └─ FastAPI sidecar (copilot/)
            ├─ /chat  — Week 1: orchestrator → OpenEMR FHIR tools (OAuth2
            │           passthrough) → deterministic verifier → audit trail
            ├─ /documents — Claude vision → strict draft schema → lineage
            │           stamping → document store
            ├─ /ask   — LangGraph supervisor: intake worker → evidence worker
            │           (hybrid RAG over guideline corpus) → answerer
            └─ telemetry — PHI-free logs, /metrics + /dashboard, Langfuse
                        traces (HIPAA host), correlation id on every response
```

Design docs: [PRD](../PRD.md) · [ARCHITECTURE](../ARCHITECTURE.md) ·
[USERS](../USERS.md) · [AUDIT](../AUDIT.md) · [observability](OBSERVABILITY.md)
· [cost analysis](COST_ANALYSIS.md) · [load test](loadtest/RESULTS.md)

## Honest limitations & next steps

- **Auth**: the demo-token button is a demo-environment affordance (password
  grant, server-side). The production path is OAuth **authorization-code +
  PKCE**; longer term, a session-trusting PHP bridge inside OpenEMR would
  remove per-user OAuth entirely for in-chart use.
- **Durability**: sessions and the audit trail are in-memory (single
  instance). Production needs Redis-backed sessions and an append-only audit
  store with retention.
- **Streaming**: responses are non-streamed JSON; SSE would cut perceived
  latency on long turns.
- **Clinical rules**: the interaction/dose rule set is a small demonstration
  corpus, not a pharmacist-reviewed source, and the bundled guideline corpus
  is paraphrased demo content — neither is clinical decision support.
