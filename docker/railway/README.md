# Deploying OpenEMR to Railway

Railway runs **one container per service** (there is no `docker-compose` on the
platform), so an OpenEMR deploy is two Railway services in the same project:

1. **`openemr`** — this repository, built from
   [`docker/railway/Dockerfile`](./Dockerfile) (a thin wrapper around the
   official `openemr/openemr` release image). Configured by
   [`railway.json`](../../railway.json) at the repo root.
2. **`MySQL`** — Railway's managed MySQL (or MariaDB) database plugin.

## 1. Create the database service

In your Railway project: **New → Database → Add MySQL**. Railway provisions it
and exposes these variables on the MySQL service:
`MYSQLHOST`, `MYSQLPORT`, `MYSQLDATABASE`, `MYSQLUSER`, `MYSQLPASSWORD`,
`MYSQL_ROOT_PASSWORD`.

## 2. Create the OpenEMR service

**New → GitHub Repo → this repo.** Railway reads `railway.json`, builds
`docker/railway/Dockerfile`, and deploys. Then add the environment variables
below to the **openemr** service. Use Railway [reference
variables](https://docs.railway.com/guides/variables#reference-variables) (the
`${{ MySQL.VAR }}` syntax) so credentials stay in sync with the database
service — replace `MySQL` with the actual name of your database service.

| OpenEMR variable          | Value                              | Notes |
|---------------------------|------------------------------------|-------|
| `MYSQL_HOST`              | `${{ MySQL.MYSQLHOST }}`            | Required. Use the database service's **private** host. |
| `MYSQL_PORT`             | `${{ MySQL.MYSQLPORT }}`            | Required if not 3306. |
| `MYSQL_ROOT_PASS`        | `${{ MySQL.MYSQL_ROOT_PASSWORD }}` | Required — OpenEMR bootstraps the schema as root on first boot. |
| `MYSQL_DATABASE`         | `${{ MySQL.MYSQLDATABASE }}`        | Database name (e.g. `railway`). |
| `MYSQL_USER`             | `${{ MySQL.MYSQLUSER }}`            | Optional; defaults to `openemr`. |
| `MYSQL_PASS`             | `${{ MySQL.MYSQLPASSWORD }}`        | Optional; defaults to `openemr`. |
| `OE_USER`                | `admin`                            | Initial OpenEMR admin login. |
| `OE_PASS`                | *(a strong password)*              | **Change this** — do not ship the `pass` default. |

Optional hardening / tuning (all read by the official image):

- `OPENEMR_SETTING_rest_api` = `1` — enable the REST API used by the
  Clinical Co-Pilot feature.
- `FORCE_DATABASE_SSL_CONNECT` = `1` — require TLS to the database.
- `REDIS_SERVER` = `${{ Redis.REDISHOST }}` — offload PHP sessions to a Redis
  service (add one the same way as MySQL) for multi-replica setups.

## 3. Persistent storage (important)

The official image keeps per-site config, generated keys, and uploaded
documents under `/var/www/localhost/htdocs/openemr/sites`. Add a **Railway
Volume** to the openemr service mounted at that path, otherwise a redeploy
wipes the installation and OpenEMR re-runs first-time setup.

Recommended mount path:
`/var/www/localhost/htdocs/openemr/sites`

## 4. First boot

First boot runs the automated installer (schema load + asset setup) and can
take a few minutes. Once the service is `Online`, open its public URL and log
in with `OE_USER` / `OE_PASS`. Verify readiness at
`https://<your-service>/meta/health/readyz` — it returns JSON with
`"status":"ready"` once the schema is loaded and the database is reachable.

> **No Railway healthcheck is configured** (`railway.json` has no
> `healthcheckPath`). The `/meta/health/readyz` endpoint is healthy over HTTPS,
> but OpenEMR 302-redirects HTTP→HTTPS, so Railway's internal *plain-HTTP* probe
> never sees a 2xx and would fail every deploy. Liveness is governed by the
> restart policy instead. If you re-add a healthcheck, point it at an endpoint
> that answers 2xx over plain HTTP, or it will block deploys.

> **Stale leader lock:** `docker/railway/Dockerfile` overrides the container
> command to remove `sites/docker-leader` before starting. `openemr.sh` creates
> that file to elect a leader and deletes it via an exit trap; an ungraceful
> stop (crash/OOM/redeploy) leaves it on the volume, and older image versions
> then crash-loop with `can't create .../docker-leader: File exists`. Clearing
> it at boot is safe for a single replica and does not re-trigger install (that
> is tracked separately by `sites/docker-completed`).

## Keeping in sync with production

The image digest in `docker/railway/Dockerfile` is pinned to the same digest as
[`docker/production/docker-compose.yml`](../production/docker-compose.yml). When
you bump the OpenEMR image there, bump it here too so Railway and the reference
production stack stay identical.
