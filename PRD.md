# AgentForge Clinical Co-Pilot — PRD (v1)

## Summary

The Clinical Co-Pilot is a **read-only, multi-turn AI assistant** embedded in
OpenEMR as a chart sidebar. It helps an inpatient hospice nurse read and
interpret a patient's record faster — surfacing what changed, current symptom
and comfort-med status, and goals of care — so she can act and document with
less time spent scanning tabs. It never writes to the chart.

The product is defined by one constraint above all others: **it may only state
what the patient's record supports.** Every clinical claim is traceable to a
source record, claims that violate clinical rules (allergy, interaction, dosage
thresholds) are flagged or blocked, and when the agent cannot verify an answer or
a tool fails, it degrades to a clearly-labeled fallback rather than guessing.
A confident wrong answer in this setting can directly harm a patient, so scope is
kept deliberately narrow: read-only, one patient at a time, sample data only.

**Target user and use cases are defined in [`USER.md`](USER.md), which is the
source of truth.** This PRD defines *what* the product must do; `USER.md` defines
*for whom and why*, and [`ARCHITECTURE.md`](ARCHITECTURE.md) defines *how*.

## Problem Statement

A hospice nurse manages several rapidly-changing patients and repeatedly opens
each chart to answer the same questions — what changed since last shift, what is
the current symptom burden, what comfort meds are due or were last given, and
what are this patient's goals of care. OpenEMR holds all of this, but it is
fragmented across notes, MAR, labs, vitals, allergies, and problems, requiring
manual synthesis under time pressure. The problem is not missing data; it is
fast, reliable, *trustworthy* retrieval and interpretation of what matters right
now.

## Scope

**In scope (v1):**

- Read-only chart interpretation via a sidebar assistant.
- Multi-turn conversation scoped to a single open patient chart session.
- Sample OpenEMR data only.
- Data types: demographics, encounters, notes, medications (incl. PRN/last-dose
  history), allergies, labs, vitals, problems, and goals-of-care / code status
  where available.

**Out of scope (v1):**

- Any write action: charting, orders, prescriptions, autonomous changes.
- General medical advice or open-ended clinical Q&A.
- Cross-patient queries or memory that persists across patients or shifts.
- Replacing existing OpenEMR chart views.

## Product Requirements

### Interaction & UX

- Embedded as a sidebar inside the OpenEMR patient chart; the nurse never leaves
  the chart to use it.
- Multi-turn: follow-up questions retain the prior turns' context within the
  current chart session (justified by the iterative use cases in `USER.md`).
- Answers are concise, scannable, and **show their sources** inline so the nurse
  can click through and confirm before acting.
- **Latency target:** streaming first content within ~1–2s; full verified answer
  within a p95 of ~8–10s at the workstation. Speed-vs-completeness is managed by
  answering from a cheap patient summary first and only chaining deeper retrieval
  when the question requires it.

### Authorization & Access Control

- The agent has **no independent identity model.** It acts as the signed-in
  OpenEMR user, using OpenEMR's REST/FHIR API and OAuth2 scopes as the
  authorization boundary.
- Every request is scoped to the **active patient chart.** Requests for another
  patient, or for data the user is not authorized to see, are refused and logged.
- Fail closed: if identity or patient scope cannot be confirmed, the agent does
  not answer.

### Verification & Trust (both halves required)

1. **Source attribution.** Every patient-specific or clinical claim must map to a
   specific retrieved source record. A claim that cannot be attributed is not
   stated as fact. Information from outside the record is explicitly labeled as
   outside the record.
2. **Domain-constraint enforcement.** Responses are checked against rule-based
   clinical constraints relevant to hospice comfort care — allergy conflicts,
   drug interactions, and dosage-threshold flags. A response that violates a
   constraint is flagged or blocked, not shown as-is.

Verification is **deterministic**: retrieved records are the only permitted
source of fact, and a post-generation check maps claims to source IDs and runs
the clinical rule checks. The model proposes; the verifier decides what reaches
the nurse.

### Failure & Fallback

- On verification failure, missing data, or tool failure, return the **most
  recent verified visit history**, clearly labeled as fallback — better than
  silence, never a guess.
- Errors are transparent to the nurse and never expose internal detail.

### Observability & Evaluation

- Every request carries a **correlation ID** through the sidebar, agent, tool
  calls, and model interactions so a full trace can be reconstructed from logs.
- Tracing/metrics/cost via **Langfuse**: request count, error rate, p50/p95
  latency, tool-call and retry counts, verification pass/fail rate, token cost.
- An eval suite exercises **boundaries** (missing data, empty record, malformed
  input), **invariants** (every claim cites a source; no cross-patient leakage),
  and **regressions** — not just happy paths.

### Security, PHI & HIPAA

- Patient data is PHI; it is transmitted over TLS, scoped to the active patient,
  and **never logged in the clear** (logs carry IDs and metadata, not PHI bodies).
- Only the minimum necessary context is sent to the LLM. We operate under the
  assumed BAA with the LLM provider (no data used for training).
- Sample/demo data only for the duration of the project.

## Architecture at a Glance

Full detail lives in [`ARCHITECTURE.md`](ARCHITECTURE.md); the committed
decisions are:

- **Topology:** a separate **Python / FastAPI** sidecar service; the OpenEMR
  sidebar calls it. Keeps AI, observability, and eval tooling out of the PHP
  monolith.
- **Data access:** the **OpenEMR REST/FHIR API** (OAuth2 scopes), reusing
  OpenEMR's authorization as the trust boundary.
- **Model & framework:** **Claude via the Anthropic SDK** in a thin, controllable
  tool-use loop.
- **Verification:** deterministic source-attribution + rule-based clinical checks
  (above).
- **Contracts:** strict input/output schemas (Pydantic) for every tool, treated
  as the source of truth over the implementation.
- **Ops:** separate `/health` (process alive) and `/ready` (OpenEMR, LLM
  provider, and Langfuse reachable) endpoints.

## Success Criteria

- The nurse gets a useful, concise, **source-cited** answer from the sidebar
  without leaving the chart, within the latency target.
- No unsupported factual claims and no cross-patient data leakage reach the user.
- Responses that would violate a clinical constraint are caught.
- The system degrades safely and legibly when data, tools, or verification fail.
- The v1 scope stays narrow enough to be fully explained across `PRD.md`,
  `USER.md`, and `ARCHITECTURE.md`, and defensible in interview.

## Open Questions

- Which OpenEMR sample-data fields actually hold goals-of-care / code status, and
  are they structured enough to cite? (Resolve in the audit — highest-severity
  data-quality risk for this user.)
- Which clinical rule set / source powers the domain-constraint checks in v1
  (a curated hospice comfort-med rule list vs. an external interaction dataset)?
- When source records conflict, which source type wins for verification priority?
- Does the sidebar open with suggested prompts, or stay empty until asked?
