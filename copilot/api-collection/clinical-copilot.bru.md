# Clinical Co-Pilot — Runnable API Collection

A grader can run every core workflow from here without reading source. Import
the Bruno files in this folder, or use the equivalent `curl` below. Set:

- `{{baseUrl}}` = `https://copilot-early-sub.up.railway.app` (deployed) or `http://localhost:8055` (local)
- `{{token}}`  = an OpenEMR OAuth2 access token (see "Get a token")
- `{{patient}}` = a patient UUID (from `GET {{openemr}}/apis/default/fhir/Patient`)
- `{{clinician}}` = any clinician id string, e.g. `nurse-maria`

## Get a token (OpenEMR password grant, demo only)

```bash
curl -sk -X POST "{{openemr}}/oauth2/default/token" \
  -d grant_type=password -d client_id={{clientId}} -d client_secret={{clientSecret}} \
  -d user_role=users -d username=admin -d password=pass \
  -d 'scope=openid api:fhir user/Patient.read user/Condition.read user/Encounter.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/DocumentReference.read'
```

## 1. Liveness — `GET /health`

```bash
curl {{baseUrl}}/health
# 200 {"status":"ok"}
```

## 2. Readiness — `GET /ready`

```bash
curl -i {{baseUrl}}/ready
# 200 {"status":"ready","checks":{"openemr":"ok","anthropic":"ok","langfuse":"ok"}}
# 503 if any dependency is unreachable
```

## 3. Chat — verified turn — `POST /chat`

```bash
curl -i -X POST {{baseUrl}}/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {{token}}" \
  -H "X-Clinician-Id: {{clinician}}" \
  -d '{"patient_id":"{{patient}}","message":"What is this patient'\''s code status and goals of care?","session_id":"demo-1"}'
# 200 {"answer":"... [src: ...]","citations":[...],"warnings":[...],"degraded":false,"correlation_id":"..."}
```

## 4. Chat — multi-turn follow-up (same session_id)

```bash
curl -s -X POST {{baseUrl}}/chat -H "Content-Type: application/json" \
  -H "Authorization: Bearer {{token}}" -H "X-Clinician-Id: {{clinician}}" \
  -d '{"patient_id":"{{patient}}","message":"And what are her active medications?","session_id":"demo-1"}'
```

## 5. Chat — cross-patient refusal is invisible to the caller by design

The scope guard blocks any tool call naming another patient; the agent simply
cannot retrieve out-of-chart data. Ask about the active patient only.

## 6. Auth failure — `POST /chat` without a token → 401

```bash
curl -i -X POST {{baseUrl}}/chat -H "Content-Type: application/json" \
  -H "X-Clinician-Id: {{clinician}}" \
  -d '{"patient_id":"{{patient}}","message":"hi","session_id":"demo-1"}'
# 401
```

## 7. Session conflict — reuse a session_id with a different patient → 409

```bash
curl -i -X POST {{baseUrl}}/chat -H "Content-Type: application/json" \
  -H "Authorization: Bearer {{token}}" -H "X-Clinician-Id: {{clinician}}" \
  -d '{"patient_id":"a-different-patient","message":"hi","session_id":"demo-1"}'
# 409
```

## 8. Chat UI — `GET /ui`

Open `{{baseUrl}}/ui` in a browser: paste token, patient id, clinician id, and chat.

## 9. Week 2 — ingest a lab PDF → `POST /documents`

Any lab-report PDF/photo works (Claude vision reads the real document):

```bash
curl -s -X POST {{baseUrl}}/documents -H "X-Clinician-Id: {{clinician}}" \
  -F "file=@your_lab_report.pdf;type=application/pdf" \
  -F "patient_id={{patient}}" -F "doc_type=lab_pdf"
# 200: results[] with values, abnormal flags, confidence, verbatim-quote
# citations anchored to document_id, and page bounding boxes
```

## 10. Week 2 — ingest an intake form → `POST /documents`

```bash
curl -s -X POST {{baseUrl}}/documents -H "X-Clinician-Id: {{clinician}}" \
  -F "file=@your_intake_form.pdf;type=application/pdf" \
  -F "patient_id={{patient}}" -F "doc_type=intake_form"
# 200: fields[] with sections and citations; blank fields come back as
# low-confidence nulls — visible, never invented
```

## 11. Week 2 — grounded, cited answer → `POST /ask`

```bash
curl -s -X POST {{baseUrl}}/ask -H "Content-Type: application/json" \
  -H "X-Clinician-Id: {{clinician}}" \
  -d '{"patient_id":"{{patient}}","question":"What changed in this patient'\''s labs?"}'
# 200: routing[] (supervisor handoffs), citations[], patient_facts vs
# guideline_evidence with hybrid-RAG provenance (keyword/dense/rerank scores)
```

Ask about a patient with no documents → `degraded: true`, a labeled refusal.

## 12. Week 2 — readiness three-state walk → `GET /ready`

```bash
curl -s {{baseUrl}}/ready
# components: document_store, vector_index (bm25 + dense, chunk count),
# reranker — each named with ok|degraded|down; externals gate 503
```

## 13. Auth — PKCE discovery → `GET /auth/config`

```bash
curl -s {{baseUrl}}/auth/config
# enabled + client_id + OpenEMR authorize_url; the browser flow runs from
# the UI ("Sign in with OpenEMR") — secret never leaves the server
```

## 14. Documents UI — `GET /ui/documents`

Open `{{baseUrl}}/ui/documents`: upload box + cited ask view with extraction
tables, retrieval provenance, and the supervisor routing trace.
