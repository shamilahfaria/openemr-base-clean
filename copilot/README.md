# Clinical Co-Pilot — Sidecar

Read-only, multi-turn AI agent for OpenEMR (hospice nurse). FastAPI service;
reads patient data via OpenEMR FHIR with OAuth2 passthrough; deterministic
verification; safe fallback; PHI-free telemetry + HIPAA audit trail.

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

## Tests & evals

```bash
pytest                 # 241 tests, ~1s (strict TDD, LLM faked)
python -m evals.run    # 16/16 boundary/invariant/regression evals -> evals/RESULTS.md
python -m loadtest.run --url http://localhost:8055 --requests 500   # -> loadtest/RESULTS.md
```

## Artifacts

- [`evals/`](evals/) — dataset + runner + [`RESULTS.md`](evals/RESULTS.md) (16/16)
- [`loadtest/RESULTS.md`](loadtest/RESULTS.md) — 0% errors at 10 & 50 concurrent
- [`api-collection/`](api-collection/) — runnable Bruno collection
- [`COST_ANALYSIS.md`](COST_ANALYSIS.md) — dev spend + 100/1K/10K/100K projections
- [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) — 3–5 min demo script

## Deployment status

- **OpenEMR base:** live — https://openemr-production-96cd.up.railway.app
- **Sidecar:** Railway service `copilot` is provisioned but its build source is
  misconfigured (currently building the OpenEMR image instead of this
  `copilot/Dockerfile`). Fix: in the Railway `copilot` service settings, set the
  source to this repo with **root directory = `copilot/`** (or disconnect the
  GitHub source and redeploy via `railway up` from `copilot/`). The image itself
  is correct and the service runs cleanly locally and in Docker
  (`docker build -t copilot . && docker run -p 8080:8080 -e OPENEMR_FHIR_BASE_URL=... -e ANTHROPIC_API_KEY=... copilot`).
