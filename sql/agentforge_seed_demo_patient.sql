-- =============================================================================
-- AgentForge Clinical Co-Pilot — demo patient code-status seed
-- =============================================================================
-- Companion to sql/agentforge_seed_code_status.sql, which tags the 15 highest
-- pids. The scripted demo uses a specific richly-seeded patient (14 active
-- meds, documented allergies) that falls outside that window; this file gives
-- that one patient a hospice-coherent code status + goals of care so UC1/UC2
-- are demonstrable on the same chart as the medication/allergy flows.
--
-- Idempotent: re-running deletes this seed's rows (tagged by note) first.
--
-- Usage (Railway early-sub):
--   railway ssh --service MySQL -- \
--     'MYSQL_PWD=$MYSQL_ROOT_PASSWORD mysql -uroot openemr' \
--     < sql/agentforge_seed_demo_patient.sql

SET @demo_uuid = UNHEX(REPLACE('a2390997-1e8c-4c41-99f5-676ad433d365', '-', ''));
SET @demo_pid = (SELECT `pid` FROM `patient_data` WHERE `uuid` = @demo_uuid);

DELETE FROM `patient_treatment_intervention_preferences`
WHERE `note` = 'AgentForge demo patient seed';

-- Code status: LOINC 81329-5 — DNR
INSERT INTO `patient_treatment_intervention_preferences`
    (`patient_id`, `observation_code`, `observation_code_text`, `value_type`,
     `value_code`, `value_code_system`, `value_display`,
     `effective_datetime`, `status`, `note`)
SELECT @demo_pid, '81329-5', 'Thoughts on resuscitation (CPR)', 'coded',
       'LA33471-6', 'http://loinc.org',
       'No CPR (Do Not Attempt Resuscitation)', NOW(), 'final',
       'AgentForge demo patient seed'
WHERE @demo_pid IS NOT NULL;

-- Goals of care: LOINC 75773-2 — comfort measures only
INSERT INTO `patient_treatment_intervention_preferences`
    (`patient_id`, `observation_code`, `observation_code_text`, `value_type`,
     `value_code`, `value_code_system`, `value_display`,
     `effective_datetime`, `status`, `note`)
SELECT @demo_pid, '75773-2',
       'Goals, preferences, and priorities for medical treatment [Reported]',
       'coded', '395093009', 'http://snomed.info/sct',
       'Prefers comfort measures only', NOW(), 'final',
       'AgentForge demo patient seed'
WHERE @demo_pid IS NOT NULL;

-- Back-fill uuids so the FHIR Observation carries a citable `id`.
UPDATE `patient_treatment_intervention_preferences`
SET `uuid` = UNHEX(REPLACE(UUID(), '-', ''))
WHERE (`uuid` IS NULL OR `uuid` = '')
  AND `note` = 'AgentForge demo patient seed';

SELECT `patient_id`, `observation_code`, `value_display`
FROM `patient_treatment_intervention_preferences`
WHERE `note` = 'AgentForge demo patient seed';
