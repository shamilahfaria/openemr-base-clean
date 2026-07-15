# PRD — Clinical Co-Pilot, Week 2: Multimodal Evidence Agent

**Product:** Clinical Co-Pilot (Week 2 expansion of the Week 1 OpenEMR agent)
**Sprint:** 1 week. Checkpoints: Architecture Defense (T+4h), MVP (Tue 11:59 PM CT), Early Submission (Thu 11:59 PM CT), Final (Sun noon CT).
**Hard gate:** Eval-driven CI is non-negotiable. Graders will introduce a regression; if the CI gate does not block it, Week 2 does not pass.

## 1. Problem & Scenario

A physician preps for a follow-up visit. Structured OpenEMR data exists, but the important recent information is buried in a **scanned lab PDF** and a **patient intake form** uploaded by the front desk. The physician asks: *"What changed, what should I pay attention to, and what evidence supports the recommendation?"*

Week 2 adds two capabilities to the Week 1 agent: (1) it can **see** real-world clinical documents, and (2) it can **route work across a small multi-agent graph** without losing grounding. Answers must remain useful when the scan is imperfect, the record is incomplete, or the user asks a follow-up.

## 2. Users

Same as Week 1: clinicians (physician/nurse) at a small practice, prepping for or during a patient visit. Demo/synthetic data only.

## 3. Functional Requirements (extracted from assignment)

### FR-1 Document ingestion & extraction (Stage 1, Core Req 1)
- FR-1.1 Implement `attach_and_extract(patient_id, file_path, doc_type)` (or equivalent tool).
- FR-1.2 Support two document types: `lab_pdf` and `intake_form`.
- FR-1.3 Store the source document in OpenEMR (no duplicate/untraceable records — must round-trip cleanly).
- FR-1.4 Extract structured JSON under a strict schema; persist derived facts as appropriate FHIR resources / OpenEMR records.
- FR-1.5 Link every derived fact back to its source document (lineage).
- FR-1.6 Unsupported/hallucinated extracted facts must be *visible* (schema + source links + verification make them detectable).

### FR-2 Structured schemas (Core Req 2)
- FR-2.1 Pydantic (strict) schemas; raw VLM output must never bypass validation — the schema is the source of truth.
- FR-2.2 `lab_pdf` fields (minimum): test name, value, unit, reference range, collection date, abnormal flag, source citation.
- FR-2.3 `intake_form` fields (minimum): demographics, chief concern, current medications, allergies, family history, source citation.
- FR-2.4 Schema validation tests committed; any schema change from Week 1 carries a migration note.

### FR-3 Hybrid RAG + rerank (Stage 2, Core Req 3)
- FR-3.1 Small clinical-guideline corpus (self-sourced) reflecting agreed practices — chronic care: HTN / T2DM / lipids (ADA, ACC/AHA, USPSTF).
- FR-3.2 Keyword (sparse) + dense vector retrieval; rerank candidates with Cohere Rerank (or equivalent).
- FR-3.3 Only top grounded evidence snippets, with source metadata, are fed to the answer model.
- FR-3.4 (Stretch) ColQwen2 / multi-vector indexing; contextual retrieval improvements (chunking, query rewriting, domain filters).

### FR-4 Supervisor + two workers (Stage 3, Core Req 4)
- FR-4.1 One supervisor + `intake-extractor` worker + `evidence-retriever` worker, built on an inspectable orchestration framework (**LangGraph** chosen).
- FR-4.2 Supervisor decides: when extraction is needed, when evidence retrieval is needed, when the final answer is ready.
- FR-4.3 Handoffs are explicit, logged, and explainable — the supervisor must never be a black box.
- FR-4.4 (Stretch) Critic agent that rejects uncited claims or unsafe action suggestions.

### FR-5 Citation contract (Core Req 5)
- FR-5.1 Every clinical claim carries machine-readable citation metadata, minimum shape: `{source_type, source_id, page_or_section, field_or_chunk_id, quote_or_value}`.
- FR-5.2 Answers separate **patient-record facts** from **guideline evidence**; a med/lab claim without a source is unacceptable.
- FR-5.3 A visual **PDF bounding-box overlay** is required.
- FR-5.4 (Stretch) Click-to-source UI with document preview.

### FR-6 Eval-driven CI gate (Stage 4, Core Req 6)
- FR-6.1 50-case golden set (synthetic/demo) exercising extraction, evidence retrieval, citations, refusals, and missing-data behavior.
- FR-6.2 Boolean rubrics (not 1–10). Required categories: `schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs`.
- FR-6.3 PR-blocking Git hook / CI: build fails if any category regresses >5% or drops below its pass threshold.
- FR-6.4 Judge configuration and results committed with the dataset; golden set reproducible from the repo alone.

### FR-7 Observability & cost (Core Req 7)
- FR-7.1 Per encounter, log: tool sequence, latency by step, token usage, cost estimate, retrieval hits, extraction confidence, eval outcome. No raw PHI in logs, traces, screenshots, or eval data.
- FR-7.2 Correlation ID propagates through ingestion, worker handoffs, VLM/retrieval calls, and FHIR writes; a full multi-agent trace is reconstructable from the correlation ID alone.
- FR-7.3 Distributed tracing: each worker invocation is a child span of the supervisor span; extraction/retrieval sub-calls nest within worker spans.
- FR-7.4 Dashboard: ingestion count, extraction field-level pass rate, retrieval hit rate, routing decisions, eval pass/fail per category, latency, errors, cost.
- FR-7.5 Alerts: extraction failure rate, RAG retrieval latency, eval regression (>5% drop in any category), each with documented response actions.
- FR-7.6 SLOs for document ingestion and evidence retrieval (p95 targets); all outbound LLM/retrieval calls have timeouts + retries.

### FR-8 Integration & delivery (Stage 5)
- FR-8.1 Week 2 flow exposed in the deployed app (publicly accessible), source-grounded UI.
- FR-8.2 `/health` and `/ready` separated; `/ready` validates document storage, vector index, reranker reachability, and returns degraded (not binary) status.
- FR-8.3 OpenAPI 3.0 spec for all Week 2 endpoints, committed, with contract tests; API collection (Bruno/Postman) updated to run every Week 2 workflow.
- FR-8.4 Integration tests over the full ingestion→answer path with fixture documents and stubbed LLM/VLM responses; must pass in CI without live API access.
- FR-8.5 README clearly separates Week 1 baseline from Week 2 behavior; graders can run the core flow without guessing branch/env/service.
- FR-8.6 Data model documented for extracted labs, intake facts, guideline chunks, citation records — each with owner (data authority), lineage, access control, validation rules; one source of truth per data type, no silent overwrites.
- FR-8.7 Backup & recovery documented (automatic + manual, RPO/RTO); golden set reproducible from repo.
- FR-8.8 Cost & latency report: actual dev spend, projected production cost, p50/p95, bottleneck analysis; baseline CPU/mem/latency/throughput vs Week 1.
- FR-8.9 Demo video (3–5 min): upload, extraction, evidence retrieval, citations, eval results, observability.

### Stretch deliverables (explicitly listed)
Critic agent; click-to-source UI; third document type (referral fax / med list); lab trend chart from extracted Observations; contextual retrieval improvements.

## 4. Non-Functional Requirements
- HIPAA-minded: demo/synthetic data only; prompts, extracted fields, images, traces, screenshots treated as sensitive; PHI-detection check in CI.
- Week 1 debt documented and resolved before adding surface area (chart-parser `.coding[].display` fallback is a P0 precondition).
- Architecture stays small enough to reason about — "narrower and stronger" beats framework sprawl.

## 5. Out of Scope (v1)
Five+ document types; write-back of orders/prescriptions; real PHI; multi-vector/ColQwen2 indexing (stretch only); full medical-document AI platform.

## 6. Success Criteria
1. Grader uploads lab PDF + intake form → grounded, cited answer with patient-fact vs guideline-evidence separation and bounding-box overlay.
2. Injected regression → CI gate fails the build.
3. Full trace reconstructable from one correlation ID; dashboard shows system health without reading logs.
