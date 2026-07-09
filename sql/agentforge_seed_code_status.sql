-- =============================================================================
-- AgentForge Clinical Co-Pilot — Code Status / Goals-of-Care seed
-- =============================================================================
-- Synthea (openemr-cmd import-random-patients) does NOT populate code status or
-- goals-of-care (AUDIT.md D4) — the single most important field for a hospice
-- nurse. This seed adds a clinically-coherent spread of code-status and
-- goals-of-care rows to the most recently created patients, so UC1/UC2 are
-- demonstrable and the get_goals_of_care tool has real data to ground on.
--
-- Data lands in `patient_treatment_intervention_preferences`, which OpenEMR
-- surfaces via FHIR `Observation?category=treatment-intervention-preference`
-- (the endpoint ARCHITECTURE.md's get_goals_of_care tool uses — NOT Goal/Consent).
-- Codes/answers are taken verbatim from the value sets seeded in sql/database.sql
-- (LOINC 81329-5 resuscitation; LOINC 75773-2 goals).
--
-- Idempotent: re-running deletes prior seed rows (tagged by note) and re-inserts.
-- `uuid` is left NULL — OpenEMR's UuidRegistry back-fills it on first FHIR read.
--
-- Usage (against a running OpenEMR database):
--   mysql -u <user> -p <openemr_db> < sql/agentforge_seed_code_status.sql
-- Adjust the LIMIT below to match how many patients you want tagged.
-- =============================================================================

-- Which patients to see (run separately to inspect before/after):
--   SELECT pid, fname, lname, DATE(date) AS created FROM patient_data ORDER BY pid DESC LIMIT 20;

-- --- Idempotency: clear any previous run of this seed ------------------------
DELETE FROM `patient_treatment_intervention_preferences`
WHERE `note` = 'AgentForge hospice seed';

-- --- Code status: LOINC 81329-5 "Thoughts on resuscitation (CPR)" ------------
-- Hospice-realistic mix: ~1 in 4 full code (Yes CPR), the rest DNR/DNAR.
INSERT INTO `patient_treatment_intervention_preferences`
    (`patient_id`, `observation_code`, `observation_code_text`, `value_type`,
     `value_code`, `value_code_system`, `value_display`,
     `effective_datetime`, `status`, `note`)
SELECT
    p.pid,
    '81329-5',
    'Thoughts on resuscitation (CPR)',
    'coded',
    CASE WHEN p.pid % 4 = 0 THEN 'LA33470-8' ELSE 'LA33471-6' END,
    'http://loinc.org',
    CASE WHEN p.pid % 4 = 0 THEN 'Yes CPR'
         ELSE 'No CPR (Do Not Attempt Resuscitation)' END,
    NOW(),
    'final',
    'AgentForge hospice seed'
FROM (SELECT pid FROM `patient_data` ORDER BY pid DESC LIMIT 15) AS p;

-- --- Goals of care: LOINC 75773-2 "Goals/preferences for medical treatment" --
-- Coherent with the CPR value above: full code -> full resuscitation;
-- everyone else spread across limited resuscitation / comfort measures only.
INSERT INTO `patient_treatment_intervention_preferences`
    (`patient_id`, `observation_code`, `observation_code_text`, `value_type`,
     `value_code`, `value_code_system`, `value_display`,
     `effective_datetime`, `status`, `note`)
SELECT
    p.pid,
    '75773-2',
    'Goals, preferences, and priorities for medical treatment [Reported]',
    'coded',
    CASE p.pid % 4
        WHEN 0 THEN '385643006'   -- Prefers full resuscitation
        WHEN 1 THEN '385644000'   -- Prefers limited resuscitation
        ELSE        '395093009'   -- Prefers comfort measures only
    END,
    'http://snomed.info/sct',
    CASE p.pid % 4
        WHEN 0 THEN 'Prefers full resuscitation'
        WHEN 1 THEN 'Prefers limited resuscitation'
        ELSE        'Prefers comfort measures only'
    END,
    NOW(),
    'final',
    'AgentForge hospice seed'
FROM (SELECT pid FROM `patient_data` ORDER BY pid DESC LIMIT 15) AS p;

-- --- Verify -------------------------------------------------------------------
--   SELECT patient_id, observation_code, value_display
--   FROM patient_treatment_intervention_preferences
--   WHERE note = 'AgentForge hospice seed'
--   ORDER BY patient_id, observation_code;
