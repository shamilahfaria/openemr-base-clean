# AgentForge — Clinical Co-Pilot

A **read-only, multi-turn AI clinical co-pilot** embedded in
[OpenEMR](https://open-emr.org), built for one narrow user: an **inpatient
hospice nurse** who needs fast, source-cited patient context between rooms. The
agent answers only what the patient's record supports, cites every clinical
claim, and degrades safely when it cannot verify an answer.

> Built on a fork of [`Gauntlet-HQ/openemr-base-clean`](https://github.com/Gauntlet-HQ/openemr-base-clean)
> for the Gauntlet AI AgentForge project.

**🌐 Deployed app:** **https://openemr-production-96cd.up.railway.app** (live on
Railway) · log in with the configured admin account.

---

## Project Documentation

The planning and audit deliverables are the source of truth for this project:

| Doc | What it covers |
|-----|----------------|
| [`PRD.md`](PRD.md) | Product requirements — what the co-pilot must do |
| [`AUDIT.md`](AUDIT.md) | Stage-3 audit of the OpenEMR base (security, architecture, performance, data quality, compliance) |
| [`USER.md`](USER.md) | Target user (hospice RN), workflow, and use cases — *why*, for *whom* |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | How the agent is built: sidecar, verification, observability, MVP build order |

## Architecture Overview

The Clinical Co-Pilot is a **separate Python / FastAPI sidecar** that runs
alongside OpenEMR — AI, observability, and evaluation tooling stay out of the PHP
monolith. The OpenEMR chart sidebar is a thin client that sends the nurse's
question (and the signed-in user's OAuth2 token) to the sidecar's `/chat`
endpoint.

Key decisions (full detail in [`ARCHITECTURE.md`](ARCHITECTURE.md)):

- **Data access:** OpenEMR's **REST / FHIR API** with the user's OAuth2 bearer
  token (authorization_code + PKCE).
- **Patient scoping:** enforced **in the sidecar** — the audit found OpenEMR does
  not enforce patient-level authorization for a clinical-user token
  ([`AUDIT.md`](AUDIT.md) S1).
- **Reasoning:** Claude via the Anthropic SDK, in a thin tool-use loop. The model
  never speaks directly to the nurse.
- **Verification:** deterministic — every claim must map to a retrieved source
  record, plus rule-based clinical checks (allergy / interaction / dosage).
- **Failure handling:** on any verification or tool failure, fall back to a
  clearly-labeled recent-visit summary.
- **Observability:** correlation ID per request into **Langfuse** (PHI-free); a
  separate append-only HIPAA audit trail records what PHI was disclosed to the LLM.

```
Nurse ─▶ OpenEMR sidebar ─▶ FastAPI sidecar
            (patient + token)      │
                                   ├─ Patient Scope Guard (active patient only)
                                   ├─ Agent Orchestrator (Claude tool-use loop)
                                   ├─ Read-only FHIR/REST tools ─▶ OpenEMR API
                                   ├─ Deterministic Verifier (attribution + rules)
                                   └─ Fallback ─▶ recent visit history
```

## Running Locally

**Prerequisite:** Docker.

```bash
cd docker/development-easy
docker compose up --detach --wait
```

| Service | URL |
|---------|-----|
| OpenEMR (HTTP) | http://localhost:8300/ |
| OpenEMR (HTTPS) | https://localhost:9300/ |
| phpMyAdmin | http://localhost:8310/ |

Login: **`admin` / `pass`**

Tests and tooling run through `openemr-cmd` (see [`CONTRIBUTING.md`](CONTRIBUTING.md)
for install). From any directory:

```bash
openemr-cmd unit-test        # alias: ut
openemr-cmd php-log          # alias: pl  (view PHP error log)
```

### Sample patient data

The committed base ships only demographic sample rows, and the standard OpenEMR
demo dataset is dated ~2017 (see [`AUDIT.md`](AUDIT.md) D1/D2). To populate the
instance with realistic, **current-dated** patients:

```bash
openemr-cmd import-random-patients 100
```

This uses [Synthea](https://github.com/synthetichealth/synthea) to generate
synthetic patients — with encounters, medications, conditions, allergies, labs,
and vitals — as CCDA and import them. First run downloads Synthea + a Java runtime;
each patient takes a few seconds.

> **Note:** Synthea does not produce hospice-specific patients or **code status /
> goals-of-care** data (OpenEMR's `patient_treatment_intervention_preferences`,
> [`AUDIT.md`](AUDIT.md) D4). That hospice-critical field is seeded separately.

## Deployment

The app is **deployed live on Railway** (project `openemr-copilot`) using the
committed [`railway.json`](railway.json) → [`docker/railway/Dockerfile`](docker/railway/Dockerfile),
with a managed **MySQL** service and a persistent volume for `sites/`. The agent
sidecar will deploy to the same infrastructure. Environment variables are set in
the Railway service (see [`docker/railway/README.md`](docker/railway/README.md)
and [`docker/railway/.env.railway.example`](docker/railway/.env.railway.example)) —
notably `OPENEMR_SETTING_rest_api=1` to enable the REST/FHIR API the Co-Pilot uses.

**Live URL:** https://openemr-production-96cd.up.railway.app

---

## About the OpenEMR Base

[OpenEMR](https://open-emr.org) is a Free and Open Source electronic health
records and medical practice management application.

- API: [`API_README.md`](API_README.md) · FHIR: [`FHIR_README.md`](FHIR_README.md)
  · Docker: [`DOCKER_README.md`](DOCKER_README.md)
- Contributing to upstream: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Security policy: [`.github/SECURITY.md`](.github/SECURITY.md)

### For developers building OpenEMR from source

Node.js 24.* is required:

```bash
composer install --no-dev
npm install
npm run build
composer dump-autoload -o
```

### License

[GNU GPL v3](LICENSE). OpenEMR is © its contributors; this fork preserves all
upstream copyright and licensing.
