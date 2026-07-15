# Week 2 Demo Runbook — Multimodal Clinical Co-Pilot

A 3–5 minute terminal-driven demo of the deployed MVP. Every step is real: real
Claude vision on a real PDF, against the live deployment.

- **Agent:** https://copilot-early-sub.up.railway.app
- **Sample PDF:** `copilot/fixtures/sample_lab_report.pdf` (synthetic, no PHI)

Set once:
```bash
B=https://copilot-early-sub.up.railway.app
PDF=copilot/fixtures/sample_lab_report.pdf
PID=demo-jane
```

## Scene 1 — Multimodal ingestion (the headline)
> "I upload a lab PDF. Claude reads it with vision and returns **validated,
> cited** facts — not free text."

```bash
curl -s -X POST "$B/documents" \
  -F "file=@${PDF};type=application/pdf" \
  -F "patient_id=${PID}" -F "doc_type=lab_pdf" \
  -H "X-Clinician-Id: nurse-maria" | python3 -m json.tool
```
Point at: `result_count: 6`; each result has `abnormal_flag`, `confidence`, and
a `citation` with the **verbatim quote** + page, anchored to `document_id`. Note
the schema is strict — a hallucinated field would fail validation, never pass
through.

## Scene 2 — Grounded, cited answer via the multi-agent graph
> "Now I ask a question. A LangGraph supervisor routes across workers, and the
> answer is grounded in the extracted facts with citations — and I can **see
> every routing decision**."

```bash
curl -s -X POST "$B/ask" -H "Content-Type: application/json" \
  -H "X-Clinician-Id: nurse-maria" \
  -d "{\"patient_id\":\"${PID}\",\"question\":\"What changed and what should I pay attention to?\"}" \
  | python3 -m json.tool
```
Point at: `routing` = `intake → evidence → answer` (inspectable, not a black
box); `citations` on every fact; `patient_facts` vs `guideline_evidence`
separated; `degraded: false`. Ask about a patient with no docs → `degraded:
true`, a safe refusal instead of invention.

## Scene 3 — The eval gate (the hard requirement)
> "Behaviour is protected by an eval gate. This is the layered eval model from
> class — unit/schema at the bottom, an LLM-scored golden set gating CI."

```bash
cd copilot && python -m evals.week2.runner    # -> GATE PASSED
git log --oneline | grep -iE "gate|answerer" # red gate landed BEFORE the answerer
```
Then show the grader's scenario — inject a regression, watch the gate catch it:
```bash
# temporarily break grounding, run the gate, see it FAIL, then revert
git stash list  # (or edit app/graph/nodes.py answerer to drop citations)
python -m evals.week2.runner   # -> GATE FAILED (regression caught)
```
See `evals/week2/README.md` for the 6-layer mapping.

## Scene 4 — Observability
> "Every request carries a correlation ID across the whole flow."

Show the `correlation_id` echoed in the `/documents` and `/ask` responses, then:
```bash
railway logs --service copilot | grep <correlation-id>
```
The Week 1 `/chat` path also exports a PHI-free **Langfuse trace** per turn and a
live dashboard at `$B/dashboard`.

## Scene 5 — One app, both weeks
`$B/chat` (Week 1 clinical Q&A), `$B/documents` + `$B/ask` (Week 2 multimodal).
`GET $B/` redirects to the chat UI; `GET $B/health` and `$B/ready` are green.
