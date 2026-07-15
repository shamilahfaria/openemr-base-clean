# Week 2 Architecture — Multimodal Evidence Agent

Builds directly on the Week 1 stack: FastAPI Python sidecar on Railway, OpenEMR (FHIR R4) as the system of record, Claude as the reasoning model, deterministic verifier (source attribution + clinical rules), PHI-free structured telemetry, correlation-ID audit trail, pytest + deterministic eval harness. Week 2 adds sight (document ingestion), a small LangGraph multi-agent graph, hybrid RAG, and a PR-blocking eval gate.

## 1. System Overview

```
                        ┌──────────────────────────── Copilot Sidecar (FastAPI, Railway) ───────────────────────────┐
 Clinician ──/ui──────▶ │  POST /documents (upload)          POST /chat (question)                                  │
 (upload + ask)         │        │                                  │                                               │
                        │        ▼                                  ▼                                               │
                        │  ┌───────────────── LangGraph: SUPERVISOR ─────────────────┐                              │
                        │  │  typed AgentState (Pydantic) · routing decisions logged  │                             │
                        │  │     │                          │                         │                             │
                        │  │     ▼                          ▼                         ▼                             │
                        │  │  INTAKE-EXTRACTOR         EVIDENCE-RETRIEVER        ANSWERER (+ verifier)              │
                        │  │  Claude vision →          hybrid RAG:               patient facts vs guideline         │
                        │  │  strict Pydantic          BM25 (SQLite FTS5)        evidence, citation contract,       │
                        │  │  (lab_pdf, intake_form)   + dense embeddings        safe fallback (Week 1 verifier     │
                        │  │  + bbox coordinates       + Cohere Rerank           extended to doc citations)         │
                        │  └─────┬────────────────────────┬─────────────────────────────────────────────────────── ┘
                        │        ▼                        ▼                                                         │
                        │   OpenEMR FHIR             Guideline index                                                │
                        │   DocumentReference        (SQLite: chunks +                                              │
                        │   + Observations /         FTS5 + vectors,                                                │
                        │   derived facts            rebuilt from repo)                                             │
                        └────────────────────────────────────────────────────────────────────────────────────────  ┘
 Observability: correlation ID → supervisor span → worker child spans → LLM/retrieval/FHIR sub-spans → dashboard + 3 alerts
 CI: GitLab pipeline + pre-push hook → pytest + schema/contract tests + 50-case golden evals → blocks >5% category regression
```

## 2. Document Ingestion Flow (Stage 1)

`attach_and_extract(patient_id, file_path, doc_type)`:
1. **Store first, extract second.** Upload the original to OpenEMR as a FHIR `DocumentReference` bound to the patient. The DocumentReference ID becomes `source_id` for all lineage — idempotency key = content hash + patient, preventing duplicate records on retry.
2. **Extract.** Claude vision reads the PDF/image page-by-page; output is forced through a strict Pydantic schema (`LabReportExtraction`, `IntakeFormExtraction`) with per-field `source citation {source_type, source_id, page_or_section, field_or_chunk_id, quote_or_value}` **and bounding-box coordinates** captured at extraction time (this is what powers the overlay — designed in, not bolted on).
3. **Validate.** `extra="forbid"`, typed units/dates, abnormal-flag enum. Fields the model can't ground get `confidence: low` + null value rather than invention — unsupported facts are visible, never silently accepted. Raw VLM output never bypasses the schema.
4. **Persist derived facts.** Lab values → FHIR `Observation`s with `derivedFrom → DocumentReference`. Intake facts → tagged records. One writer path; OpenEMR remains the single source of truth for clinical data.

## 3. Multi-Agent Graph (Stage 3) — LangGraph

- **Supervisor** node owns a typed `AgentState` (Pydantic): question, patient context, pending work, gathered facts/evidence, citations. It routes: *needs extraction?* → intake-extractor; *needs guideline evidence?* → evidence-retriever; *enough grounded material?* → answerer. Every routing decision is a structured log event (`worker`, `reason`, `state_summary`) — inspectable, not a black box.
- **intake-extractor** wraps the ingestion flow (§2) as a graph node.
- **evidence-retriever** wraps hybrid RAG (§4).
- **Answerer + verifier** reuses the Week 1 deterministic verifier, extended so every claim must resolve to either a patient-record citation (FHIR/document) or a guideline citation — mixed-provenance answers render the two classes separately in the UI.
- Handoff payloads are typed contracts; supervisor↔worker interface has contract tests in CI.
- *(Stretch)* Critic node between answerer and response that rejects uncited claims / unsafe suggestions.

## 4. Hybrid RAG (Stage 2)

- **Corpus:** ~12–18 chronic-care guideline documents (ADA Standards of Care, ACC/AHA hypertension & cholesterol, USPSTF screening) — freely available, directly matched to lab-PDF values (A1c, lipid panel, BP). Chunked by section with title-context prepended; stored in repo → index is fully reproducible (`make build-index`), which also satisfies the backup/recovery requirement.
- **Retrieval:** BM25 via SQLite FTS5 + dense cosine over stored embeddings → union of top-k from each → **Cohere Rerank** → top 5 snippets with `{doc, section, chunk_id}` metadata to the answer model. No new infra service: one SQLite file inside the sidecar.
- Timeouts + retry with backoff on embed/rerank calls; on reranker outage, degrade to RRF-fused hybrid scores (documented failure mode, `/ready` reports degraded).

## 5. Eval Gate (Stage 4) — the HARD GATE

- **50-case golden set** in-repo (YAML): ~15 extraction (clean + noisy scans, missing fields), ~15 retrieval/citation, ~10 refusal/missing-data, ~10 end-to-end "what changed" cases.
- **Boolean rubrics:** `schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs` — deterministic checkers where possible; LLM-judge only for `factually_consistent`, with a fixed yes/no rubric and pinned judge config.
- **CI (GitLab, source of truth) + pre-push Git hook:** build → lint/typecheck → pytest (schemas, tools, contract tests, ingestion integration tests with fixture docs + stubbed LLM — no live APIs) → eval suite → dependency audit + security scan + PHI-detection scan of logs/traces. **Fails if any rubric category drops >5% vs the recorded baseline or below its threshold.** Baseline scores are committed; regressions are diffs, not vibes.

## 6. Observability & Operations

- Correlation ID (Week 1) propagates: request → supervisor span → worker child spans → VLM/retrieval/FHIR sub-spans. Full trace reconstructable from the ID alone.
- New structured events: `doc_ingest_start/complete`, `extraction_field_outcome`, `retrieval_hit/miss`, `worker_handoff`, `eval_run`. Same Week 1 log schema, PHI-free (IDs and hashes, never values/text).
- Dashboard (Langfuse — closes Week 1 P1 gap): request/error/latency p50/p95, ingestion count, field-level extraction pass rate, retrieval hit rate, routing decisions, eval pass rate per category, cost per encounter.
- **Alerts (3):** extraction failure rate >20% / 15 min; retrieval p95 >3 s; eval category regression >5% — each documented with a response action.
- SLOs: ingestion p95 <30 s; evidence retrieval p95 <3 s; `/ready` checks doc storage, vector index, reranker and returns per-dependency degraded status.
- OpenAPI 3.0 spec (FastAPI-generated, committed, contract-tested); Bruno collection covers upload, extraction status, retrieval, full flow.

## 7. Data Authority & Lineage

| Data type | Source of truth | Lineage | Write access |
|---|---|---|---|
| Source documents | OpenEMR DocumentReference | upload event, content hash | ingestion tool only |
| Extracted lab Observations | OpenEMR FHIR | `derivedFrom` → DocumentReference + page/bbox | ingestion tool only |
| Intake facts | OpenEMR | citation → DocumentReference field | ingestion tool only |
| Guideline chunks | Git repo (rebuildable index) | doc + section + chunk_id | build step only |
| Citations / traces / evals | Sidecar (SQLite / repo) | correlation ID | agent runtime / CI |

No silent overwrites: re-ingesting the same document is idempotent; changed documents create new versions, never in-place edits.

## 8. Key Risks & Tradeoffs

- **LangGraph adoption mid-week** — mitigated by keeping the graph tiny (4 nodes) and wrapping existing Week 1 tool code as nodes rather than rewriting it.
- **VLM hallucination** — strict schemas + per-field confidence + bbox-anchored citations + `factually_consistent` eval category make invention visible and regression-tested.
- **Scan quality** — golden set includes deliberately noisy fixtures; low-confidence fields degrade to "needs human review," never guesses.
- **Cost/latency** — extraction is the expensive step; it runs once per document (cached by content hash), not per question.
- **Deferred:** ColQwen2/multi-vector, third doc type, critic agent, click-to-source UI — stretch only after the gate is green.

## 9. Week Plan

- **Mon–Tue (MVP):** schemas + `attach_and_extract` + LangGraph skeleton + minimal golden set wired into CI (gate exists from day 1).
- **Wed–Thu (Early sub):** hybrid RAG + rerank, citation contract end-to-end, 50 cases complete, dashboards + alerts.
- **Fri–Sun (Final):** bbox overlay UI polish, cost/latency report, demo video, stretch items only if green.
