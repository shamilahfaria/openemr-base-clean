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
