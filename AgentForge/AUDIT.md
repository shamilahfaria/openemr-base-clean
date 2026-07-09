# AgentForge Clinical Co-Pilot — OpenEMR Audit (Stage 3)

## Executive Summary

This audit examined the OpenEMR codebase across five passes — security,
architecture, performance, data quality, and compliance — from the perspective of
adding a read-only AI agent that reads patient data through OpenEMR's REST/FHIR
API. Six findings are load-bearing for any such integration.

**1. OpenEMR has no patient-level authorization for clinical-user tokens
(CRITICAL).** Access control is role-based on `section|subsection` only;
`AclMain::aclCheckCore` takes no patient/encounter/facility argument
(`src/Common/Acl/AclMain.php:166`). A nurse's token can read *every* patient the
role permits — FHIR `user/*` scope is explicitly "multiple patients"
(`FHIR_README.md:170`). The only native per-patient pin is the SMART
`patient/`-launch token, designed for a patient viewing their *own* chart, which
fits an agent traversing a caseload poorly. **Consequence: an external read
integration must enforce per-patient scoping itself — OpenEMR will not.**

**2. The OAuth2 passthrough model is viable (validated).** OpenEMR runs a
standards-based OAuth2 + SMART-on-FHIR provider; register the sidecar as a
confidential client using authorization_code + PKCE with least-privilege
`user/<Resource>.read` scopes (`src/RestControllers/AuthorizationController.php`).
Do not use the password grant (disabled by default).

**3. "Last-administered" medication timing does not exist in the data model
(HIGH).** `prescriptions` captures orders only — dose, route, PRN flag, interval
— but no administration event; there is no MAR table
(`sql/database.sql:8698`). **Consequence: an agent can report what is ordered
(drug, dose, PRN flag, interval) but never when a dose was administered.**

**4. Goals-of-care / code status IS reachable via FHIR (assumption corrected,
positive).** Not through `Goal`/`Consent` (which would return wrong data or 404),
but as US Core `Observation` with `category=treatment-intervention-preference`
(DNR/CPR, LOINC 81329-5), backed by `patient_treatment_intervention_preferences`.
**But** this is a recent schema addition unlikely to be populated in the demo
data (see #5).

**5. The shipped demo data is almost empty and stale (CRITICAL/blocking).** Only
14 demographic rows are committed (`sql/example_patient_data.sql`); zero clinical
INSERTs. The "standard" demo dataset is fetched externally at build time from a
2017-era OpenEMR 5.0.0 snapshot (`docker/flex/Dockerfile:274`) — all dates ~2017,
predating the coded advance-directive/code-status fields. **Consequence: any
"recent activity" view is meaningless on static 2017 data; fresh, current-dated
data must be loaded or generated before the dataset can ground an agent.**

**6. Compliance gaps an integrating agent must close (HIGH).** OpenEMR does log
reads (good), but attributes API reads to the *service account*, not the end user
driving the request; never records the onward disclosure to an external LLM;
stores full PHI request/response bodies in `api_log` in plaintext by default
(`api_log_option=2`); and has no log retention/purge. **Consequence: an external
agent must emit its own per-request audit chain (end-user identity → patient →
tool calls → PHI disclosed to the LLM → outcome) and keep PHI out of third-party
trace/observability tools.**

Performance is favorable for read integrations: per-patient reads are indexed, but
there is a large fixed per-request tax (full `globals` reload, per-service UUID
back-fill scan, zero read-path caching), so **call count dominates latency** —
caching a per-patient summary in the integrating service is strongly justified.

---

## 1. Security

**S1 — No patient-level access control on clinical-user tokens. Severity:
CRITICAL.**
`AclMain::aclCheckCore($section,$value,$user,$return_value)` has no
patient/encounter axis (`src/Common/Acl/AclMain.php:166`). REST gates only on
section (`apis/routes/_rest_routes_standard.inc.php:100`,
`request_authorization_check("patients","demo")` then fetches any `:puuid`). FHIR
clinical-user requests fall to a role-only `else` branch
(`apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:610`); only SMART
`patient/` context pins one UUID (`src/Common/Http/HttpRestRouteHandler.php:65`).
*Impact:* an inherited nurse token exposes the whole permitted patient
population. *Fix:* enforce the nurse's active census in the sidecar; reject/redact
out-of-scope `pid`/`uuid`.

**S2 — `restrict_user_facility` does not gate API patient reads. Severity: High.**
Global exists (`library/globals.inc.php:957`) but is used only for UI dropdowns
(`library/patient.inc.php:265`), not in `PatientService`/RestControllers.
*Impact:* facility restriction cannot be relied on to bound the agent. *Fix:*
scope in the sidecar.

**S3 — DB credentials committed in plaintext, not gitignored. Severity: High.**
`sites/default/sqlconf.php:6` (`$login`/`$pass` cleartext, git-tracked);
production compose ships `MYSQL_ROOT_PASSWORD: root`
(`docker/production/docker-compose.yml:13`). *Impact:* limited for us (we use the
API), but easy pivot on a co-located deploy. *Fix:* ensure prod overrides; keep
the sidecar off any shared secret store containing them.

**S4 — `HelpfulDie()` echoes SQL + backtrace to the browser by default. Severity:
High.** `library/sql.inc.php:381` prints failing statement + `debug_backtrace()`;
suppression flag off by default (`library/globals.inc.php:2107`). Statements
often carry PHI. *Fix:* the sidecar must never forward raw upstream error bodies;
treat non-2xx as opaque.

**S5 — OAuth2 provider is standards-based (enabling, informational).**
authorization_code + PKCE (`AuthorizationController.php:309,674`), refresh via
`offline_access`, access-token TTL 1h / refresh 3mo (`:110-111`). Password grant
disabled by default (`:736`). *Use:* confidential client, authorization_code +
PKCE, minimal `user/<Resource>.read` + `offline_access`.

**S6 — PHI in URLs and audit-log rows. Severity: Medium.** `pid` in GET query
strings (`interface/main/main_screen.php:477`) lands in web-server logs; full SQL
+ bind values stored in `log.comments`
(`src/Common/Logging/EventAuditLogger.php:446`). *Fix:* pass identifiers in
headers/body; scrub PHI from sidecar logs and LLM request metadata.

**Also Medium:** Twig autoescape globally off
(`src/Common/Twig/TwigContainer.php:70`) — escape OpenEMR free-text before
render; core session cookie `HttpOnly=false`/`Secure=false`
(`src/Common/Session/SessionConfigurationBuilder.php:88`) — rely on the hardened
OAuth token path, not the web session.

**Well-handled (positive):** bcrypt/Argon2 password storage
(`src/Common/Auth/AuthHash.php:52`); parameterized SQL via ADODB binds
(`library/sql.inc.php:96`); HMAC CSRF tokens (`src/Common/Csrf/CsrfUtils.php:37`);
per-install RSA-2048 OAuth keys, never committed
(`src/Common/Auth/OAuth2KeyConfig.php:63`).

## 2. Architecture

Layered PHP monolith: new code `/src/` (PSR-4, service layer extends
`src/Services/BaseService.php`), legacy `/library/`, UI `/interface/`. The correct
integration surface for us is the **HTTP API**, not internal calls: `/apis/default/api`
(proprietary REST) and `/apis/default/fhir` (**FHIR R4 4.0.1, US Core, SMART v2.2**),
both OAuth2-protected (`_rest_routes.inc.php:32`).

**Data-type → table → service → API map:**

| Data type | Table | Service | FHIR |
|-----------|-------|---------|------|
| Demographics | `patient_data` | `FhirPatientService` | `GET /fhir/Patient` |
| Encounters | `form_encounter` | `FhirEncounterService` | `GET /fhir/Encounter` |
| Notes | `form_clinical_notes`, `pnotes` | `ClinicalNotesService` | `GET /fhir/DocumentReference` (`pnotes` NOT EXPOSED) |
| Medications (+PRN) | `prescriptions`, `lists` | `FhirMedicationRequestService` | `GET /fhir/MedicationRequest` (last-administered NOT EXPOSED) |
| Allergies | `lists` type=allergy | `AllergyIntoleranceService` | `GET /fhir/AllergyIntolerance` |
| Labs | `procedure_order/report/result` | `FhirObservationLaboratoryService` | `GET /fhir/Observation?category=laboratory` |
| Vitals | `form_vitals` | `FhirObservationVitalsService` | `GET /fhir/Observation?category=vital-signs` |
| Problems | `lists` type=medical_problem | `FhirConditionService` | `GET /fhir/Condition` |
| Goals-of-care / code status | `patient_treatment_intervention_preferences` | `FhirObservationTreatmentInterventionPreferenceService` | `GET /fhir/Observation?category=treatment-intervention-preference` |

**A1 — Code status / goals-of-care ARE FHIR-reachable (HIGH, corrects our
assumption).** Via `Observation` category `treatment-intervention-preference`
(`src/Services/FHIR/FhirObservationService.php:72`), coded DNR/CPR/comfort-measures
options seeded in schema (`sql/database.sql:15296`). Scanned directives via
`DocumentReference`. *Use `Observation`, not `Goal`.*

**A2 — `Goal`/`CarePlan` are a decoy for code status (Medium).** They read
encounter care-plan goals (`src/Services/CarePlanService.php:45`), unrelated to
DNR; no `/fhir/Consent` exists. *Exclude from the code-status tool.*

**A3 — No medication administration record (HIGH for inpatient hospice).** Meds
come from `prescriptions` (orders); the only dispense source is `drug_sales`
pharmacy POS, not nurse administration. No `MedicationAdministration` route.
*Impact:* "last-administered" is unrepresentable — descope. Surface `prescriptions.prn`
+ `sig`/`interval` only.

**A4 — `pnotes` and `patient_data.completed_ad` flag have no API (Low-Medium).**
Rely on `DocumentReference` + `Observation`, not the `completed_ad` boolean.

**A5 — US Core version ambiguity (Low/verify).** Route file names US Core 3.1.0
while `FHIR_README.md:50` claims 8.0 — verify against live `GET /fhir/metadata`.

**Integration recommendation:** use **FHIR R4** as the primary surface (covers 8/9
tools, standards-versioned), fall back to proprietary REST only for gaps (raw SOAP
notes). Do not call `src/Services` directly from the Python sidecar.

## 3. Performance

Per-patient reads are indexed on the primary filter (`patient_data.pid` UNIQUE;
`pid` keys on `form_encounter`, `prescriptions`, `procedure_order`, `form_vitals`,
`forms`, `pnotes`, `lists`) — row selection is cheap. Latency risk is fixed
per-request overhead + assembly, not the filter.

**P1 — Full `globals` table reloaded on every request, no cache (High).**
`interface/globals.php:450` runs `SELECT ... FROM globals` (~700+ rows) every
request. *Impact:* unavoidable baseline on every tool call. *Fix:* minimize call
count; reuse connections; cache the patient summary.

**P2 — `UuidRegistry::createMissingUuidsForTables()` on every FHIR service
construction (High).** `COUNT(*)` scan (+ possible `UPDATE`) across whole tables
before returning data (`src/Common/Uuid/UuidRegistry.php:434`; called in 28
service constructors). *Impact:* per-call tax; first read of each resource type
after import is slow. *Fix:* fewer service-crossing calls; measure on live DB.

**P3 — `procedure_result` has no patient key → mandatory multi-level join for labs
(Medium-High).** Labs require joining `procedure_order`→`procedure_report`→
`procedure_result` + ~8 more tables (`src/Services/ProcedureService.php:163`).
*Impact:* labs are the heaviest tool. *Fix:* treat as expensive; fetch once, cache,
limit by date.

**P4 — `lists` overloaded, no composite `(pid,type)` index, un-indexed `ORDER BY
date` filesort (Medium).** `src/Services/ListService.php:50`. *Fix:* fold
allergies/problems/meds into one cached summary fetch.

**P5 — No read-path caching anywhere (informational).** `BaseService::search()`
selects all fields, no cache in the clinical read path
(`src/Services/BaseService.php:487`). *Impact:* caching must live in our sidecar.

**Latency guidance for the Co-Pilot:** the dominant lever is **call count, not row
count**. Cheap live tools: demographics, encounters, vitals, single list reads.
Expensive: labs/observations (P3), any multi-service sweep. **A cached
patient-summary is strongly warranted** — on patient open, fan out once
(demographics + problems + allergies + meds + recent vitals + recent labs),
assemble a compact summary in the sidecar, and serve subsequent turns from cache
to stay inside the 1–2s first-content / p95 <8–10s targets.

## 4. Data Quality

**D1 — No clinical demo data committed; only 14 demographic rows (CRITICAL).**
`sql/example_patient_data.sql` = demographics only; zero INSERTs into `lists`,
`prescriptions`, `form_vitals`, `procedure_result`, `form_encounter`, `pnotes`.
*Impact:* with only committed data, every clinical query is empty → UC1/UC2/UC3
fail. *Fix:* do not rely on committed data.

**D2 — "Standard" demo data is external, stale (2017), unauditable from the repo
(CRITICAL/blocking).** Fetched at build time from an OpenEMR 5.0.0 snapshot
(`docker/flex/Dockerfile:274`, `docker/flex/utilities/devtoolsLibrary.source:234`),
then schema-upgraded. *Impact:* (a) can't verify completeness without loading;
(b) predates coded advance-directive/med-intent fields → NULL after upgrade;
(c) all dates ~2017 → "recent"/"last 48h" windows return nothing. *Fix:* load it
and profile row counts, **or** generate fresh synthetic hospice patients (Synthea
or hand-authored SQL) with current dates. Recommended: synthetic, current-dated.

**D3 — No medication-administration timing (High; confirms A3).** `prescriptions`
has `prn` but no administration timestamp (`sql/database.sql:8698`); the only
`administered_*` columns belong to `immunizations`. *Impact:* UC2 "last given"
uncomputable. *Fix:* never claim administration times; surface PRN flag + interval.

**D4 — Code status has schema/vocabulary but no seeded patient values (High).**
Categories and coded options exist; no patient rows populate them. *Impact:* the
single most important hospice field is blank in shipped data. *Fix:* author
code-status data for test patients.

**D5 — Overloaded `lists` + free-text-vs-coded inconsistency (Medium).** `title`
(free text) coexists with `diagnosis`/`rxnorm_drugcode` (often NULL). *Impact:*
unreliable normalization/cross-checking. *Fix:* always filter `lists.type`
explicitly; prefer coded columns, fall back to free text, never infer a code.

**D6 — Duplicate-record potential (Low-Medium).** Acknowledged in repo
(`tests/Tests/Services/DuplicatePatientDetectionTest.php`; a merge utility exists);
repeated SSNs in the example file. *Fix:* resolve patients by `pid`, never by name.

**UC support given available data:** UC1 (recent context) **at risk** — static 2017
data has no "recent"; UC2 (pre-med review) **structurally capped** — no admin
timing, no seeded code status; UC3 (fallback) **best-supported** — but only if
UC1/UC2 honestly gate on missing fields instead of hallucinating.

## 5. Compliance & Regulatory (HIPAA)

**C1 — OpenEMR logs data ACCESS, not just changes (positive baseline).** SELECTs
routed through `library/ADODB_mysqli_log.php:50` →
`EventAuditLogger::auditSQLEvent`; `audit_events_query` on by default. Satisfies
the §164.312(b) baseline for app-DB reads — but see C2.

**C2 — API reads attributed to the OAuth client, not the clinician; patient-id
attribution fragile (High).** `authUser` = service account; SQL-event `pid` read
from `$_SESSION['pid']`, defaults to 0 for stateless FHIR
(`EventAuditLogger.php:508`; `ApiResponseLoggerListener.php:75`). *Impact:* logs
read "service-account, patient 0" — useless for breach scoping / accounting of
disclosures. *Fix:* the Co-Pilot must log the end-user clinician + concrete patient
per call.

**C3 — `api_log` stores full request+response PHI in plaintext by default
(High).** `api_log_option` default 2 = Full (`library/globals.inc.php:2893`);
`longtext` bodies, `'encrypt' => 'No'` (`LogTablesSink.php:86`). *Impact:* every
Co-Pilot pull duplicates PHI at rest. *Fix:* set the service account to minimal
logging or ensure at-rest encryption.

**C4 — No log retention/rotation/purge anywhere (Medium-High).** No feature found
repo-wide. *Impact:* PHI accumulates unbounded; no §164.316(b)(2) policy. *Fix:*
define explicit retention in the Co-Pilot's audit store (≥6y metadata; minimal/zero
PHI payload).

**C5 — Tamper-evidence present but not tamper-proof; ATNA remote sink off by
default (Medium).** SHA3-512 checksums (`LogTablesSink.php:63`); `enable_atna_audit`
default 0. *Fix:* write Co-Pilot audit events to an append-only/WORM store or
forward to SIEM.

**C6 — Accounting-of-disclosures facility exists and is reusable (positive).**
`recordDisclosure` (`EventAuditLogger.php:567`). Sending PHI to an LLM is a
disclosure; consider recording one per patient.

**Regulatory design considerations:** (R1) **BAA** — pin to the BAA-covered
model/region, fail closed rather than route to a non-BAA fallback. (R2)
**Minimum-necessary** (§164.502(b)) — send only per-patient, per-resource,
time-windowed PHI; enforce in the tool layer. (R3) **PHI in traces** — Langfuse is
a secondary PHI sink; either bring it inside the BAA/trust boundary or
redact/tokenize before tracing. (R4) **Breach surface** — plaintext `api_log`,
LLM prompts, traces, and sidecar tokens; minimize each. (R5) **Tokens** —
short-lived, least-privilege, never in logs, rotated.

**Audit events the Co-Pilot must emit (per request):** correlation id;
authenticated end-user (clinician); patient id(s); timestamp + source; user intent
(PHI-redacted if stored outside trust boundary); each tool/FHIR call (endpoint,
resource, patient, params, status, record count); **exact PHI sent to the LLM** (a
minimum-necessary manifest, hashed/referenced not verbatim); LLM model + region
used (proves BAA routing); verification/guardrail outcome; response reference;
fail-closed events.

---

## Verify on a Live Instance (could not determine statically)

- Whether `patient_treatment_intervention_preferences` is populated in the target
  dataset (schema-present, data-dependent).
- Definitive US Core profile version via `GET /fhir/metadata` (A5).
- Whether P2's `COUNT(*)` is index-satisfied on production-sized tables, and actual
  row counts for `lists`/`procedure_result`.
- Actual completeness of the external demo dataset once loaded (D2).
