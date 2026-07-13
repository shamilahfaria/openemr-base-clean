# Clinical Co-Pilot — Sidecar

Read-only, multi-turn AI agent for OpenEMR (hospice nurse). FastAPI service;
reads patient data via OpenEMR FHIR with OAuth2 passthrough; deterministic
verification; safe fallback; PHI-free telemetry (stdout logs, live dashboard,
and Langfuse traces) + HIPAA audit trail.

Langfuse tracing turns on when `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
(optional `LANGFUSE_HOST`) are set; each turn is one PHI-free trace keyed by the
request's correlation id. Without keys the other telemetry channels are
unaffected. See [`OBSERVABILITY.md`](OBSERVABILITY.md).

See the root docs: [`../ARCHITECTURE.md`](../ARCHITECTURE.md),
[`../USER.md`](../USER.md), [`../AUDIT.md`](../AUDIT.md).

## Run locally (verified working)

```bash
cd copilot
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENEMR_FHIR_BASE_URL=http://localhost:8300/apis/default/fhir
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --port 8055
```

- Chat UI: http://localhost:8055/ui
- `GET /health` · `GET /ready` · `POST /chat`

## One-click demo token (removes the manual OAuth dance)

The bearer passed to `/chat` must be a real OpenEMR OAuth2 token. For demo
environments the UI has a **Generate demo token** button that calls
`POST /demo/token`, which runs OpenEMR's password grant server-side and returns
a FHIR-scoped access token. It is **off by default** (404) and only turns on when
these env vars are set on the sidecar:

```bash
DEMO_TOKEN_ENABLED=1
OPENEMR_BASE_URL=https://openemr-early-sub.up.railway.app   # or OPENEMR_OAUTH_TOKEN_URL
DEMO_OAUTH_CLIENT_ID=...        # a registered, enabled OpenEMR OAuth client
DEMO_OAUTH_CLIENT_SECRET=...
DEMO_OAUTH_USERNAME=admin
DEMO_OAUTH_PASSWORD=<openemr-admin-password>
DEMO_CLINICIAN=nurse-maria                                  # optional, prefills the UI
DEMO_PATIENT=a2390997-1e8c-4c41-99f5-676ad433d365           # optional, prefills the UI
```

The client secret and OpenEMR's raw error body are never returned to the browser.
The button mints a privileged admin token, so it is an **admin/test-user
affordance only**: it stays hidden unless the UI is opened with `?demo=1`, which
OpenEMR's launcher (`library/copilot.php`) adds exclusively for the demo admin
user (the username in `COPILOT_DEMO_ADMIN_USER`, default `admin`). Regular
clinicians launching the Co-Pilot never see it.

OpenEMR's own UI also links straight here (top-nav launcher + a per-patient
"Ask Clinical Co-Pilot" button on the chart) via `library/copilot.php`.

## Tests & evals

```bash
pytest                 # 242 tests, ~1s (strict TDD, LLM faked)
python -m evals.run    # 16/16 boundary/invariant/regression evals -> evals/RESULTS.md
python -m loadtest.run --url http://localhost:8055 --requests 500   # -> loadtest/RESULTS.md
```

## Artifacts

- [`evals/`](evals/) — dataset + runner + [`RESULTS.md`](evals/RESULTS.md) (16/16)
- [`loadtest/RESULTS.md`](loadtest/RESULTS.md) — 0% errors at 10 & 50 concurrent
- [`api-collection/`](api-collection/) — runnable Bruno collection
- [`COST_ANALYSIS.md`](COST_ANALYSIS.md) — dev spend + 100/1K/10K/100K projections

## Deployed (Railway, `early-sub` environment)

- **Agent:** https://copilot-early-sub.up.railway.app (`/ui`, `/health`, `/ready`, `/chat`)
- **OpenEMR:** https://openemr-early-sub.up.railway.app

Live and doing verified, source-cited turns end-to-end. Deploy with
`cd copilot && railway up --service copilot` (builds `copilot/Dockerfile` via
`copilot/railway.json`).
