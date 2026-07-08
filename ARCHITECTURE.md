# AgentForge Clinical Co-Pilot — Architecture

## High-Level Summary

The Clinical Co-Pilot is a **read-only, multi-turn AI assistant** embedded in
OpenEMR as a chart sidebar, serving one narrow user: an inpatient hospice nurse
(see [`USER.md`](USER.md)). It exists to give her a fast, **source-cited**
answer to "what changed, what's the current comfort status, what am I about to
give, and what are this patient's wishes" without scanning tabs — and to behave
predictably when data or tools are incomplete.

The system is a **separate Python / FastAPI sidecar** that runs alongside
OpenEMR, not inside the PHP monolith. This keeps AI, observability, and
evaluation tooling in a stack built for it (Pydantic, Langfuse, load tests) and
gives a clean deployment and scaling boundary. The OpenEMR sidebar is a thin
client: it holds the active patient context and the signed-in user's OAuth2
token, and calls the sidecar's `/chat` endpoint.

Five design decisions define the architecture. **First, the LLM never speaks
directly to the nurse.** Claude (via the Anthropic SDK) proposes an answer, but a
deterministic verification layer decides what is displayed. **Second,
authorization is layered** — the sidecar calls OpenEMR's REST/FHIR API with the
user's bearer token, so OpenEMR is the source of truth for identity and
*resource-level* access. But the audit found OpenEMR enforces **no patient-level**
authorization for a clinical-user token (AUDIT.md S1), so the sidecar itself
hard-scopes every request to the active patient — a primary trust boundary we
own, not an afterthought. **Third, verification is deterministic and two-sided**: every clinical
claim must map to a retrieved source record (source attribution), and every
answer is checked against a curated clinical rule set — allergy conflicts,
interactions, dosage thresholds (domain-constraint enforcement). **Fourth,
failure shrinks scope rather than inventing** — on any verification or tool
failure the agent returns a clearly-labeled fallback to the most recent verified
visit history. **Fifth, every tool has a strict Pydantic contract**, treated as
the source of truth over the implementation, so retrieval, verification, and
fallback stay predictable and testable.

The agent surface is deliberately minimal and traces to the use cases in
`USER.md`: multi-turn exists because the nurse's real questions arrive as chains
of dependent follow-ups; tool chaining exists because a safety check is itself a
chain (allergy → interaction → vital). Conversation state is scoped to a single
chart session and never persists across patients or shifts.

The primary tradeoff is **breadth for trust.** v1 does not write to the chart,
does not answer general medical questions, and does not carry cross-session
memory — those are safety decisions, not missing features. The second tradeoff
is **speed vs. completeness**, managed explicitly: the agent answers from a cheap
patient-summary tool first and streams output within 1–2s, chaining deeper
retrieval only when the question requires it, targeting a p95 end-to-end under
~8–10s at the workstation.

Observability is wired from the first request: a **correlation ID** flows through
the sidebar, agent, every tool call, and every LLM interaction into **Langfuse**,
which backs the dashboards (latency, errors, tool failures, retries, verification
pass/fail, token cost) and three alerts. Separate `/health` and `/ready`
endpoints expose liveness and real dependency checks (OpenEMR, Anthropic,
Langfuse). The goal is an agent a hospital CTO could reason about: narrow,
read-only, sidecar-scoped to one patient, deterministically verified, and fully
traceable.

## System Diagram

```mermaid
flowchart LR
  Nurse["Hospice Nurse"] -->|question / follow-up| Sidebar

  subgraph OpenEMR["OpenEMR (PHP)"]
    Sidebar["Chart Sidebar (JS widget)<br/>active patient + OAuth2 token"]
    OAuth["OAuth2 / FHIR + REST API<br/>identity + resource authz<br/>(NO patient-level authz — AUDIT S1)"]
    DB[("Patient Record Data")]
    OAuth --> DB
  end

  Sidebar -->|POST /chat<br/>{patient_id, message, session_id, bearer}| API

  subgraph Sidecar["Clinical Co-Pilot Sidecar (Python / FastAPI)"]
    API["/chat  /health  /ready"]
    Scope["Patient Scope Guard<br/>census / active-chart allow-list"]
    Session["Session Store<br/>(in-memory, chart-scoped history)"]
    Orch["Agent Orchestrator<br/>Anthropic SDK tool-use loop"]
    Tools["Tool Layer<br/>Pydantic-contracted, read-only"]
    Verify["Verification Layer (deterministic)<br/>1. source attribution<br/>2. clinical rule checks"]
    Rules["Clinical Rule Set<br/>(versioned JSON)"]
    Fallback["Fallback Logic<br/>recent visit history"]
  end

  API --> Scope --> Session --> Orch
  Orch <-->|tool calls| Tools
  Tools -->|FHIR/REST + bearer, in-scope pid only| OAuth
  Orch -->|draft + citations| Verify
  Tools -->|source records| Verify
  Rules --> Verify
  Verify -->|verified answer| API
  Verify -->|unverifiable / violation / tool failure| Fallback --> API
  Orch -->|reasoning| Claude["Claude (Anthropic API)<br/>assumed BAA"]

  API -.correlation ID.-> LF["Langfuse<br/>traces • metrics • cost • alerts"]
  Orch -.-> LF
  Tools -.-> LF
  Verify -.-> LF
  Fallback -.-> LF

  API ==>|HIPAA audit event| Audit["Audit Trail (append-only)<br/>clinician • patient • PHI-to-LLM manifest"]
  Verify ==> Audit
```

*Langfuse holds IDs/metadata only (PHI-free); the append-only Audit Trail holds
the compliance record — see §9/§10.*

## Components

### 1. OpenEMR Chart Sidebar (thin client)
A JS widget injected into the patient chart page. Responsibilities: capture the
active `patient_id` and the signed-in user's OAuth2 access token, POST turns to
the sidecar's `/chat`, and render streamed answers, citations, constraint
warnings, and the fallback label. It contains **no** agent logic.

### 2. FastAPI Sidecar (the agent service)
Owns three endpoints:
- `POST /chat` — one conversation turn (streaming response).
- `GET /health` — process liveness only.
- `GET /ready` — validates real dependencies: OpenEMR API reachable, Anthropic
  reachable, Langfuse reachable. Returns 503 if any is down.

### 3. Auth & Patient Scoping (Patient Scope Guard)
The sidecar never mints its own identity. It forwards the user's bearer token to
OpenEMR's FHIR/REST API, so OpenEMR enforces identity and **resource-level** access
(which FHIR resource types the token may read). **Critically, the audit found
OpenEMR enforces no patient-level authorization for a clinical-user token — a
nurse's token can read any patient the role permits (AUDIT.md S1).** So
patient-level scoping is *our* responsibility: a **Patient Scope Guard** hard-scopes
every request and every tool call to the active `patient_id` (and, at scale, the
nurse's census allow-list) and rejects any tool argument naming a different
patient. This is a primary trust boundary, not defense in depth. **Fail closed**
if the token is missing/expired or scope can't be confirmed.

### 4. Session Store (multi-turn state)
Conversation history keyed by `session_id` (one open chart = one session), held
**in-memory** and ephemeral for v1. No PHI is persisted beyond process lifetime;
history is dropped when the chart session ends. (Redis is the scale-out swap, not
needed for MVP.)

### 5. Agent Orchestrator
A thin Anthropic SDK tool-use loop. Per turn it: loads session history, builds a
bounded prompt with the active patient context, lets Claude call read-only tools,
and hands the draft answer + cited source IDs to the verifier. It does not let
Claude free-roam the chart; tools are the only data path. Model: Claude
Sonnet-class (latency/cost fit for structured summarization); the verifier is
**not** an LLM.

### 6. Tool Layer (read-only, Pydantic-contracted)
Small set of retrieval tools over OpenEMR FHIR/REST. Every tool returns
**structured data with source identifiers** and fails closed on malformed or
unauthorized input.

| Tool | Purpose |
|------|---------|
| `get_patient_summary(patient_id)` | Cheap orientation: demographics, active problems, recent context |
| `get_recent_encounters(patient_id)` | Recent visits / encounter metadata; backs fallback |
| `search_notes(patient_id, query)` | Relevant note excerpts with source IDs |
| `get_medications(patient_id)` | **Ordered** meds + PRN flag/interval (orders only — no administration timing exists; AUDIT D3) |
| `get_allergies(patient_id)` | Allergies and reactions |
| `get_labs(patient_id)` | Recent lab values + dates |
| `get_vitals(patient_id)` | Recent vitals / trends |
| `get_problem_list(patient_id)` | Active + historical problems |
| `get_goals_of_care(patient_id)` | Code status / goals of care via FHIR `Observation?category=treatment-intervention-preference` (**not** `Goal`/`Consent`; AUDIT A1) |

Example contract (source of truth over implementation):

```python
class MedicationRecord(BaseModel):
    source_id: str            # FHIR resource id — required for attribution
    name: str
    dose: str | None
    route: str | None
    is_prn: bool
    prn_interval: str | None    # e.g. "Q4H" — as ordered; NO administration timing (AUDIT D3)

class GetMedicationsOutput(BaseModel):
    patient_id: str
    records: list[MedicationRecord]
```

### 7. Verification Layer (deterministic, two checks)
The trust boundary. Runs **after** the draft, **before** display:
1. **Source attribution** — each patient-specific/clinical claim must map to a
   `source_id` from the retrieved records. Unmapped claims are withheld; outside-
   record content must be explicitly labeled.
2. **Clinical rule checks** — the answer (and any med it references) is checked
   against a **versioned JSON rule set**: allergy cross-check against the
   patient's own allergy list, plus a curated hospice comfort-med
   interaction/dosage-threshold table. A violation is surfaced as an explicit
   warning to the nurse (flag); an unsupported claim is blocked.

Known limits (documented deliberately): the v1 rule set is curated, not
exhaustive; attribution is record-level, not sentence-diff exact.

### 8. Fallback Logic
On verification failure, missing data, or tool error: return the most recent
verified visit history, clearly labeled as fallback. Never invents content; never
an error dump.

### 9. Observability
Correlation ID minted at `/chat`, attached to every log line, tool span, and LLM
call, exported to **Langfuse**. Captures request/error counts, p50/p95 latency,
tool-call + retry counts, verification pass/fail rate, and token cost per request.
**Langfuse holds only IDs and metadata — never PHI** (AUDIT R3): prompts, tool
payloads, and answers are redacted/tokenized before export.

### 10. HIPAA Audit Trail (separate from observability)
The audit found OpenEMR's own logs attribute API reads to the service account, not
the prompting clinician, and never record the onward disclosure to the LLM
(AUDIT.md C2). So the Co-Pilot owns its compliance audit chain: an **append-only**
(WORM / SIEM-forwardable) record per request capturing the authenticated clinician,
patient id(s), each tool/FHIR call, the **minimum-necessary PHI manifest actually
sent to the LLM** (hashed/referenced, not verbatim), the model + region used
(proves BAA routing), the verification outcome, and any fail-closed event. Distinct
from Langfuse; retained per policy (≥6y for audit metadata).

## Request Flow (one turn)

1. Nurse asks a question (or follow-up) in the sidebar.
2. Sidebar POSTs `{patient_id, message, session_id, bearer}` to `/chat`.
3. Sidecar mints a correlation ID, validates token + patient scope (fail closed).
4. Orchestrator loads session history and builds a bounded, patient-scoped prompt.
5. Claude selects and calls read-only tools; tools fetch via FHIR/REST with the
   bearer token and return records + source IDs.
6. Claude drafts an answer with citations.
7. Verifier runs source attribution + clinical rule checks against the records.
8. If clean → stream the cited answer (with any constraint warnings) to the nurse
   and append the turn to session history.
9. If not clean or any tool failed → return labeled fallback (recent visit
   history).
10. Every step is traced under the one correlation ID in Langfuse.

## Trust Boundaries

- OpenEMR owns identity and **resource-level** authz (via OAuth2/FHIR); the
  **sidecar owns patient-level scoping** — OpenEMR does not enforce it (AUDIT S1).
- The sidecar may only **read** scoped patient data; it forwards, never elevates.
- Claude may summarize but may not override source truth.
- The verifier alone decides what is displayable.
- Fallback may reduce scope but may not invent content.

## Failure Modes → Behavior

| Condition | Behavior |
|-----------|----------|
| Missing patient data | Return most complete **verified** summary available |
| Single tool fails | Skip it; retry once; fall back to visit history if answer can't be grounded |
| Verification fails | Withhold unsupported claims; fall back |
| Clinical rule violation | Surface explicit warning; block the offending claim |
| Unexpected model output | Discard unless verifiable |
| Unauthorized / cross-patient request | Deny and log; no answer |
| Dependency down (`/ready` red) | Sidebar shows degraded state; no silent 200s |

## Contracts, Endpoints & API Collection

- **Contracts:** Pydantic models for every tool I/O and for `/chat`
  request/response are the canonical schema.
- **`POST /chat`** request: `{patient_id, message, session_id}` + `Authorization`
  bearer; response: `{answer, citations[], warnings[], degraded: bool,
  correlation_id}` (streamed).
- **API collection:** a Bruno/Postman collection covering `/chat` (happy path,
  cross-patient refusal, missing-data fallback), `/health`, `/ready` — runnable
  without reading source.

## Observability, Ops & Load

- **Dashboards (Langfuse):** request count, error rate, p50/p95 latency, tool
  call counts, retry counts, verification pass/fail rate, token cost.
- **Three alerts:** p95 latency > threshold; error rate > threshold; tool
  failure rate > threshold — each with a documented on-call response.
- **Health/ready:** `/health` liveness; `/ready` checks OpenEMR + Anthropic +
  Langfuse.
- **Baselines + load:** capture CPU/memory/latency/throughput baselines; run
  load tests at 10 and 50 concurrent users, recording p50/p95/p99 + error rate.

## Evaluation Approach

Eval cases target **boundaries** (empty record, missing meds/allergies, malformed
query), **invariants** (every claim cites a source; no cross-patient leakage;
allergy-conflicting med always flagged), and **regressions**. Each case
documents the failure mode it guards. Details + results live in the eval dataset.

## Requirements Coverage (assignment)

| Requirement | Where addressed |
|-------------|-----------------|
| Agentic chatbot (multi-turn, tool-invoking) | Orchestrator + Session Store |
| Verification: source attribution | Verification §7.1 |
| Verification: domain constraints | Verification §7.2 + rule set |
| Authorization / multi-user | Inherited OAuth2 (identity/resource) + **sidecar Patient Scope Guard** (AUDIT S1) |
| Speed vs completeness | Summary-first + streaming; latency target |
| HIPAA / PHI / BAA | §10 Audit Trail; minimum-necessary to LLM; no PHI in Langfuse (AUDIT C2/R3) |
| Failure modes / graceful degradation | Failure Modes table + Fallback |
| Observability (order, timing, tool failures, tokens/cost) | Correlation ID + Langfuse |
| Correlation ID across boundaries | Minted at `/chat`, threaded everywhere |
| Canonical schemas | Pydantic contracts |
| Dashboards + 3 alerts | Observability, Ops & Load |
| /health + /ready (meaningful) | Auth §2 + Ops |
| API collection | Contracts, Endpoints & API Collection |
| Baselines + load tests | Observability, Ops & Load |
| Eval (boundaries/invariants/regression) | Evaluation Approach |

## Tradeoffs

- **Sidecar over in-PHP:** proper AI/eval/observability stack; one more service
  to deploy.
- **Inherited identity + sidecar patient-scoping:** we reuse OpenEMR's OAuth2 for
  identity/resource authz but must add our own patient-level guard, because
  OpenEMR enforces none (AUDIT S1) — a small custom control we fully own and test.
- **Deterministic verifier over LLM-judge:** defensible and testable; a curated
  rule set must be maintained and is not exhaustive.
- **Session-scoped multi-turn over cross-session memory:** natural follow-ups,
  every conversation bounded and verifiable.
- **Fallback history over blank failure:** utility under partial failure.

## MVP Build Order

1. **Sidecar skeleton:** FastAPI app with `/health`, `/ready` (real dependency
   checks), correlation-ID middleware, Langfuse wired in (PHI-redacted).
2. **Synthetic hospice data (prerequisite):** the shipped demo data is empty and
   2017-stale (AUDIT D1/D2), so generate current-dated synthetic patients with
   meds, allergies, labs, vitals, problems, encounters, and **code status**
   (`Observation` treatment-intervention-preference). Without this, nothing is
   testable.
3. **OpenEMR API access:** register the OAuth2 confidential client
   (authorization_code + PKCE); implement `get_patient_summary` end-to-end first.
4. **Patient Scope Guard:** enforce active-patient scoping in the request path and
   every tool call (AUDIT S1) — build this *before* the full tool set so scoping is
   never bolted on.
5. **Tool layer:** remaining read-only tools behind Pydantic contracts; each
   returns `source_id`s.
6. **Orchestrator:** Anthropic SDK tool-use loop + in-memory session store;
   `POST /chat` streaming.
7. **Verifier v1 + audit trail:** source-attribution check; clinical rule set (start
   with allergy cross-check, add curated interaction/dosage table); emit the HIPAA
   audit event (§10) per request.
8. **Fallback + failure paths:** wire the Failure Modes table.
9. **Sidebar widget:** inject into the OpenEMR chart; pass patient + token; render
   answer, citations, warnings, fallback label.
10. **Eval + load:** boundary/invariant/regression suite (incl. cross-patient
   refusal and code-status accuracy); baselines; 10 & 50-user load tests;
   dashboards + alerts.

## Audit Outcomes Incorporated

The Stage-3 audit ([`AUDIT.md`](AUDIT.md)) resolved this doc's prior open
assumptions:

- **OAuth2 on-behalf-of reads:** validated — authorization_code + PKCE
  confidential client (AUDIT S5).
- **Goals-of-care / code status:** exposed via FHIR `Observation`
  (treatment-intervention-preference), **not** `Goal`/`Consent` (AUDIT A1/A2).
- **Patient-level authz:** OpenEMR enforces none for clinical-user tokens → added
  the Patient Scope Guard (AUDIT S1).
- **`last-administered` timing:** not in the data model → descoped (AUDIT A3/D3).
- **Sample data:** empty / 2017-stale → synthetic-data workstream added (AUDIT D1/D2).

**Still to verify on a live instance:** whether
`patient_treatment_intervention_preferences` is populated in the target dataset;
the live US Core profile version (`GET /fhir/metadata`); and per-request read
latency under load (AUDIT P2).
