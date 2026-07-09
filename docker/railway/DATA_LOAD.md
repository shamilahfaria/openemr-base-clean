# Loading Demo Data (local + Railway)

Two datasets make the Clinical Co-Pilot demonstrable, each closing an audit gap:

1. **Synthea random patients** — realistic, **current-dated** clinical histories
   (encounters, meds, conditions, allergies, labs, vitals). Fixes
   [`AUDIT.md`](../../AUDIT.md) D1 (no clinical data) and D2 (2017-stale demo).
2. **Code-status / goals-of-care seed** — [`sql/agentforge_seed_code_status.sql`](../../sql/agentforge_seed_code_status.sql).
   Fixes AUDIT D4: Synthea does **not** produce code status, the single most
   important hospice field. Lands in `patient_treatment_intervention_preferences`,
   surfaced via FHIR `Observation?category=treatment-intervention-preference`.

> Order matters: import patients **first**, then run the seed (it tags the most
> recently created patients).

---

## Option A — Local dev stack (recommended while building/testing)

Use the `docker/development-easy` stack. Best for developing the agent and
recording a local demo; no schema-drift risk.

```bash
cd docker/development-easy
docker compose up --detach --wait

# 1. Synthea patients (downloads Synthea + JRE on first run; ~minutes for 100)
openemr-cmd import-random-patients 100

# 2. Code-status / goals-of-care seed
#    Pipe the SQL into the openemr container's mysql client. Adjust DB creds to
#    match docker/development-easy/docker-compose.yml (commonly openemr/openemr).
docker compose exec -T openemr \
  mysql -u openemr -popenemr openemr \
  < ../../sql/agentforge_seed_code_status.sql
```

Verify (should list `81329-5` / `75773-2` rows):

```bash
docker compose exec -T openemr \
  mysql -u openemr -popenemr openemr -e \
  "SELECT patient_id, observation_code, value_display
   FROM patient_treatment_intervention_preferences
   WHERE note='AgentForge hospice seed' ORDER BY patient_id;"
```

---

## Option B — Deployed Railway instance

Needed for the deployed-app demo (Early/Final). The Railway `openemr` service is
the official release image + a managed MySQL. Requires a one-time SSH key:

```bash
railway ssh keys add          # or: railway ssh keys github   (one-time)
railway status                # confirm project 'openemr-copilot' is linked
```

### B1. Code-status seed (small, safe — do this)

The container ships a `mysql` client and the DB connection vars in its
environment. Pipe the seed in over SSH:

```bash
railway ssh 'mysql -h "$MYSQL_HOST" -u root -p"$MYSQL_ROOT_PASS" "$MYSQL_DATABASE"' \
  < ../../sql/agentforge_seed_code_status.sql
```

> If `railway ssh` does not forward stdin in your CLI version, open a shell
> (`railway ssh`) and paste the SQL, or run it non-interactively with
> `mysql ... -e "<statement>"`.

### B2. Synthea patients on Railway — pick one

- **Preferred: generate locally, restore to Railway.** Run Option A locally,
  then dump and restore into the Railway MySQL (installs a local `mysql`/
  `mysqldump` client; both images track recent OpenEMR so schemas align — if
  worried, dump data-only for the clinical tables):

  ```bash
  # dump from local dev DB
  docker compose exec -T openemr mysqldump -u openemr -popenemr openemr > /tmp/openemr.sql
  # restore into Railway MySQL (opens the DB shell; needs a local mysql client)
  railway connect MySQL < /tmp/openemr.sql
  ```

- **Alternative: run the import in-container** by replicating
  `import-random-patients` over `railway ssh` (install `openjdk17-jre`, fetch the
  Synthea jar, `--exporter.ccda.export true`, then run
  `contrib/util/ccda_import/import_ccda.php`). Heavier on the live service — only
  if local generation isn't an option.

Then run **B1** to seed code status for the restored patients.

---

## Notes

- The MVP checkpoint only requires a **live deployed URL** (met). Loading data
  onto Railway matters for the **agent demo** at Early/Final — do it when you
  build the sidecar.
- DB credentials differ by environment; adjust `-u/-p` and the DB name to match.
- The seed is idempotent (re-running clears prior seed rows by their `note` tag).
