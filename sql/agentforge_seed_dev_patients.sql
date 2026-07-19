-- =============================================================================
-- AgentForge Clinical Co-Pilot — DEV environment patient seed
-- =============================================================================
-- Stands up a small, clinically-coherent patient set on a fresh (Synthea-less)
-- OpenEMR so the in-chart co-pilot has real data to ground on in the `dev`
-- Railway environment. Three patients with a deliberate spread so code-status
-- differentiation is demonstrable:
--
--   AF-DEV-001  Eleanor Hartwell   DNR + comfort measures   (hospice)
--   AF-DEV-002  Walter Krause      DNR + comfort measures   (hospice)
--   AF-DEV-003  Margaret Osei      Full code + curative     (contrast)
--
-- Each carries: demographics, code status + goals of care
-- (patient_treatment_intervention_preferences → FHIR Observation), allergies
-- and problems (lists → AllergyIntolerance / Condition), and active
-- medications (prescriptions → MedicationRequest) — the resources the chart
-- tools read.
--
-- Idempotent: patients are keyed by pubpid (inserted only if absent); all
-- child rows are tagged 'AgentForge dev seed' and cleared + re-inserted every
-- run, so re-running is safe and never duplicates.
--
-- uuids on child rows are left NULL — OpenEMR's UuidRegistry back-fills them on
-- first FHIR read (same pattern as agentforge_seed_code_status.sql). patient_data
-- uuids are set explicitly so the patients are immediately findable/navigable.
--
-- Usage (dev OpenEMR database):
--   railway ssh --environment dev --service MySQL -- \
--     'MYSQL_PWD=$MYSQL_ROOT_PASSWORD mysql -uroot openemr' < sql/agentforge_seed_dev_patients.sql
-- =============================================================================

SET @seed_tag = 'AgentForge dev seed';
SET @prov = (SELECT id FROM users WHERE authorized = 1 ORDER BY id LIMIT 1);

-- --- Patients (insert-if-absent, keyed by pubpid) ----------------------------
INSERT INTO patient_data (pid, uuid, pubpid, fname, lname, DOB, sex, date)
SELECT (SELECT COALESCE(MAX(pid), 0) + 1 FROM patient_data p),
       UNHEX(REPLACE(UUID(), '-', '')), 'AF-DEV-001', 'Eleanor', 'Hartwell',
       '1946-03-12', 'Female', NOW()
WHERE NOT EXISTS (SELECT 1 FROM patient_data WHERE pubpid = 'AF-DEV-001');

INSERT INTO patient_data (pid, uuid, pubpid, fname, lname, DOB, sex, date)
SELECT (SELECT COALESCE(MAX(pid), 0) + 1 FROM patient_data p),
       UNHEX(REPLACE(UUID(), '-', '')), 'AF-DEV-002', 'Walter', 'Krause',
       '1943-07-22', 'Male', NOW()
WHERE NOT EXISTS (SELECT 1 FROM patient_data WHERE pubpid = 'AF-DEV-002');

INSERT INTO patient_data (pid, uuid, pubpid, fname, lname, DOB, sex, date)
SELECT (SELECT COALESCE(MAX(pid), 0) + 1 FROM patient_data p),
       UNHEX(REPLACE(UUID(), '-', '')), 'AF-DEV-003', 'Margaret', 'Osei',
       '1954-11-05', 'Female', NOW()
WHERE NOT EXISTS (SELECT 1 FROM patient_data WHERE pubpid = 'AF-DEV-003');

SET @p1 = (SELECT pid FROM patient_data WHERE pubpid = 'AF-DEV-001');
SET @p2 = (SELECT pid FROM patient_data WHERE pubpid = 'AF-DEV-002');
SET @p3 = (SELECT pid FROM patient_data WHERE pubpid = 'AF-DEV-003');

-- --- Idempotency: clear prior seed child rows --------------------------------
DELETE FROM patient_treatment_intervention_preferences WHERE note = @seed_tag;
DELETE FROM lists WHERE comments = @seed_tag;
DELETE FROM prescriptions WHERE note = @seed_tag;

-- --- Code status (LOINC 81329-5) + goals of care (LOINC 75773-2) -------------
INSERT INTO patient_treatment_intervention_preferences
    (patient_id, observation_code, observation_code_text, value_type,
     value_code, value_code_system, value_display, effective_datetime, status, note)
VALUES
    (@p1, '81329-5', 'Thoughts on resuscitation (CPR)', 'coded',
     'LA33471-6', 'http://loinc.org', 'No CPR (Do Not Attempt Resuscitation)', NOW(), 'final', @seed_tag),
    (@p1, '75773-2', 'Goals, preferences, and priorities for medical treatment [Reported]', 'coded',
     '395093009', 'http://snomed.info/sct', 'Prefers comfort measures only', NOW(), 'final', @seed_tag),
    (@p2, '81329-5', 'Thoughts on resuscitation (CPR)', 'coded',
     'LA33471-6', 'http://loinc.org', 'No CPR (Do Not Attempt Resuscitation)', NOW(), 'final', @seed_tag),
    (@p2, '75773-2', 'Goals, preferences, and priorities for medical treatment [Reported]', 'coded',
     '395093009', 'http://snomed.info/sct', 'Prefers comfort measures only', NOW(), 'final', @seed_tag),
    (@p3, '81329-5', 'Thoughts on resuscitation (CPR)', 'coded',
     'LA33470-8', 'http://loinc.org', 'CPR (Attempt Resuscitation)', NOW(), 'final', @seed_tag),
    (@p3, '75773-2', 'Goals, preferences, and priorities for medical treatment [Reported]', 'coded',
     '373808002', 'http://snomed.info/sct', 'Curative care intended', NOW(), 'final', @seed_tag);

-- --- Allergies (lists type=allergy -> FHIR AllergyIntolerance) ---------------
-- OpenEMR maps AllergyIntolerance.code from the coded `diagnosis` field
-- (RXNORM), not the free-text title — without it the substance reads "Unknown".
INSERT INTO lists (pid, type, title, diagnosis, begdate, activity, comments, reaction, severity_al)
VALUES
    (@p1, 'allergy', 'Penicillin', 'RXNORM:7980', '2019-05-01', 1, @seed_tag, 'Hives', 'moderate'),
    (@p2, 'allergy', 'Sulfamethoxazole', 'RXNORM:10831', '2016-08-01', 1, @seed_tag, 'Rash', 'mild');
-- AF-DEV-003 has no known allergies (contrast case).

-- --- Problems (lists type=medical_problem -> FHIR Condition) -----------------
INSERT INTO lists (pid, type, title, diagnosis, begdate, activity, comments)
VALUES
    (@p1, 'medical_problem', 'Congestive heart failure', 'ICD10:I50.9', '2021-02-10', 1, @seed_tag),
    (@p1, 'medical_problem', 'Chronic obstructive pulmonary disease', 'ICD10:J44.9', '2020-11-03', 1, @seed_tag),
    (@p2, 'medical_problem', "Alzheimer's disease", 'ICD10:G30.9', '2022-01-15', 1, @seed_tag),
    (@p2, 'medical_problem', 'Chronic kidney disease, stage 4', 'ICD10:N18.4', '2021-09-20', 1, @seed_tag),
    (@p3, 'medical_problem', 'Malignant neoplasm of breast', 'ICD10:C50.919', '2023-06-01', 1, @seed_tag),
    (@p3, 'medical_problem', 'Essential hypertension', 'ICD10:I10', '2018-04-12', 1, @seed_tag);

-- --- Medications (prescriptions -> FHIR MedicationRequest) -------------------
-- `unit` is an integer FK to the drug_units list, so dose text lives in `dosage`.
-- txDate / usage_category_title / request_intent_title are NOT NULL without a
-- default under this DB's strict mode, so set them explicitly.
INSERT INTO prescriptions
    (patient_id, provider_id, drug, dosage, date_added, txDate,
     usage_category_title, request_intent_title, active, note)
VALUES
    (@p1, @prov, 'Furosemide', '40 mg daily', NOW(), CURDATE(), 'Community', 'Order', 1, @seed_tag),
    (@p1, @prov, 'Morphine Sulfate', '15 mg q4h PRN', NOW(), CURDATE(), 'Community', 'Order', 1, @seed_tag),
    (@p2, @prov, 'Donepezil', '10 mg daily', NOW(), CURDATE(), 'Community', 'Order', 1, @seed_tag),
    (@p2, @prov, 'Lisinopril', '10 mg daily', NOW(), CURDATE(), 'Community', 'Order', 1, @seed_tag),
    (@p3, @prov, 'Tamoxifen', '20 mg daily', NOW(), CURDATE(), 'Community', 'Order', 1, @seed_tag),
    (@p3, @prov, 'Amlodipine', '5 mg daily', NOW(), CURDATE(), 'Community', 'Order', 1, @seed_tag);

-- --- Report ------------------------------------------------------------------
SELECT p.pubpid, p.fname, p.lname,
       LOWER(CONCAT_WS('-', SUBSTR(HEX(p.uuid),1,8), SUBSTR(HEX(p.uuid),9,4),
             SUBSTR(HEX(p.uuid),13,4), SUBSTR(HEX(p.uuid),17,4), SUBSTR(HEX(p.uuid),21))) AS uuid,
       (SELECT COUNT(*) FROM lists l WHERE l.pid=p.pid AND l.type='allergy' AND l.comments=@seed_tag) AS allergies,
       (SELECT COUNT(*) FROM lists l WHERE l.pid=p.pid AND l.type='medical_problem' AND l.comments=@seed_tag) AS problems,
       (SELECT COUNT(*) FROM prescriptions rx WHERE rx.patient_id=p.pid AND rx.note=@seed_tag) AS meds,
       (SELECT COUNT(*) FROM patient_treatment_intervention_preferences t WHERE t.patient_id=p.pid AND t.note=@seed_tag) AS codestatus
FROM patient_data p WHERE p.pubpid LIKE 'AF-DEV-%' ORDER BY p.pubpid;
