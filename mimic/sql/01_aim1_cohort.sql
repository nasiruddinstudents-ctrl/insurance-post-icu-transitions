-- =============================================================================
-- 01_aim1_cohort.sql
-- Insurance and Post-ICU Transitions (MIMIC-IV v3.1) -- Aim 1 base cohort
-- Billing project: ctheg-scores   Output dataset: ctheg-scores.ins_icu
--
-- Design notes (keep in methods section):
--  * Unit = one hospital admission per patient: the patient's FIRST
--    ICU-containing admission (earliest admittime). The first admission is
--    chosen BEFORE applying survival/insurance/age filters, so later
--    admissions never substitute for an excluded first one (avoids selecting
--    on survival).
--  * Insurance = admissions.insurance as recorded for that admission
--    ("admission-recorded"), not verified coverage. No dual-eligibility
--    inference is made.
--  * Post-ICU hospital days = dischtime minus the LAST ICU outtime of the
--    admission (handles ICU readmission within the same hospitalization).
--  * Exclusion is done with flags, not WHERE filters, so the attrition table
--    (Step 2) is computed from the same table as the analysis.
--  * CASE mappings below are PROVISIONAL. Run 00_value_counts.sql first;
--    any string not listed maps to 'UNMAPPED' so nothing is silently lumped.
--  * Mappings CONFIRMED against 00_value_counts.sql output on Nov 12, 2026
--    (insurance: 6 values; discharge_location: 14 values; all mapped).
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Step 0. Output dataset (must be US multi-region to query physionet-data)
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS `ctheg-scores.ins_icu`
  OPTIONS (location = 'US');


-- ---------------------------------------------------------------------------
-- Step 1. Base table: first ICU-containing admission per patient, all ages
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE `ctheg-scores.ins_icu.aim1_base` AS
WITH icu_by_hadm AS (
  SELECT
    hadm_id,
    COUNT(*)                                          AS n_icu_stays,
    MIN(intime)                                       AS first_icu_intime,
    MAX(outtime)                                      AS last_icu_outtime,
    SUM(los)                                          AS icu_los_days_total,
    ARRAY_AGG(stay_id ORDER BY intime LIMIT 1)[OFFSET(0)]        AS first_stay_id,
    ARRAY_AGG(first_careunit ORDER BY intime LIMIT 1)[OFFSET(0)] AS first_careunit,
    ARRAY_AGG(stay_id ORDER BY outtime DESC LIMIT 1)[OFFSET(0)]  AS last_stay_id
  FROM `physionet-data.mimiciv_3_1_icu.icustays`
  GROUP BY hadm_id
),

adm AS (
  SELECT
    a.subject_id,
    a.hadm_id,
    a.admittime,
    a.dischtime,
    a.deathtime,
    a.admission_type,
    a.admission_location,
    a.discharge_location,
    a.insurance,
    a.language,
    a.marital_status,
    a.race,
    a.hospital_expire_flag,
    p.gender,
    p.anchor_year_group,
    p.anchor_age + (EXTRACT(YEAR FROM a.admittime) - p.anchor_year) AS age_at_admit,
    i.n_icu_stays,
    i.first_icu_intime,
    i.last_icu_outtime,
    i.icu_los_days_total,
    i.first_stay_id,
    i.first_careunit,
    i.last_stay_id,
    ROW_NUMBER() OVER (
      PARTITION BY a.subject_id
      ORDER BY a.admittime, a.hadm_id
    ) AS icu_adm_seq
  FROM `physionet-data.mimiciv_3_1_hosp.admissions` a
  JOIN icu_by_hadm i USING (hadm_id)                       -- ICU-containing only
  JOIN `physionet-data.mimiciv_3_1_hosp.patients` p ON p.subject_id = a.subject_id
),

first_icu_adm AS (
  SELECT * FROM adm WHERE icu_adm_seq = 1
),

base AS (
SELECT
  f.* EXCEPT (icu_adm_seq),

  -- Age group --------------------------------------------------------------
  CASE
    WHEN f.age_at_admit < 18  THEN '<18'
    WHEN f.age_at_admit <= 64 THEN '18-64'
    ELSE '65+'
  END AS age_group,

  -- Insurance (admission-recorded) -- PROVISIONAL, confirm with Q1/Q2 ----------
  CASE
    WHEN f.insurance IS NULL OR TRIM(f.insurance) = ''           THEN 'Missing'
    WHEN UPPER(TRIM(f.insurance)) = 'MEDICAID'                    THEN 'Medicaid'
    WHEN UPPER(TRIM(f.insurance)) = 'PRIVATE'                     THEN 'Private'
    WHEN UPPER(TRIM(f.insurance)) = 'MEDICARE'                    THEN 'Medicare'
    WHEN UPPER(TRIM(f.insurance)) IN ('NO CHARGE', 'SELF PAY')    THEN 'No charge/Self-pay'
    WHEN UPPER(TRIM(f.insurance)) = 'OTHER'                       THEN 'Other'
    ELSE 'UNMAPPED'
  END AS ins_cat,

  -- Discharge destination (grouped) -- PROVISIONAL, confirm with Q3 ----------
  CASE
    WHEN f.discharge_location IS NULL                                  THEN 'missing'
    WHEN UPPER(f.discharge_location) = 'DIED'                          THEN 'died'
    WHEN UPPER(f.discharge_location) = 'HOME'                          THEN 'home'
    WHEN UPPER(f.discharge_location) = 'HOME HEALTH CARE'              THEN 'home_health'
    WHEN UPPER(f.discharge_location) = 'SKILLED NURSING FACILITY'      THEN 'snf'
    WHEN UPPER(f.discharge_location) LIKE 'REHAB%'                     THEN 'rehab'
    WHEN UPPER(f.discharge_location) = 'CHRONIC/LONG TERM ACUTE CARE'  THEN 'ltach'
    WHEN UPPER(f.discharge_location) = 'HOSPICE'                       THEN 'hospice'
    WHEN UPPER(f.discharge_location) IN (
           'AGAINST ADVICE', 'ASSISTED LIVING', 'HEALTHCARE FACILITY',
           'OTHER FACILITY', 'ACUTE HOSPITAL', 'PSYCH FACILITY')       THEN 'other'
    ELSE 'UNMAPPED'
  END AS dc_group,
  (COALESCE(UPPER(f.discharge_location), '') = 'AGAINST ADVICE') AS dc_ama,         -- sensitivity flag

  -- Timing -----------------------------------------------------------------
  DATETIME_DIFF(f.dischtime, f.last_icu_outtime, MINUTE) / 1440.0 AS post_icu_days_raw,
  DATETIME_DIFF(f.dischtime, f.admittime, MINUTE) / 1440.0        AS hosp_los_days,

  -- Survival to discharge ----------------------------------------------------
  (f.hospital_expire_flag = 0
     AND f.deathtime IS NULL
     AND COALESCE(UPPER(f.discharge_location), '') <> 'DIED')     AS survived_to_dc

FROM first_icu_adm f
)

-- Analysis flags (kept in one place so the rules are easy to audit)
SELECT
  b.*,
  -- Negative gaps = ICU bed released after hospital discharge was logged.
  -- Pre-check (Nov 12, 2026), survivors: <-4h n=166 (0.2%), -4h..-1h n=1,317,
  -- -1h..0 n=6,793, 0..4h n=1,265, >4h n=66,220.
  -- Primary rule: tolerate up to 4 h negative; beyond that = invalid.
  (b.post_icu_days_raw IS NOT NULL AND b.post_icu_days_raw >= -4/24) AS time_valid,
  -- Sensitivity rule: 1 h tolerance
  (b.post_icu_days_raw IS NOT NULL AND b.post_icu_days_raw >= -1/24) AS time_valid_strict,
  CASE
    WHEN b.post_icu_days_raw IS NULL OR b.post_icu_days_raw < -4/24 THEN NULL
    ELSE GREATEST(b.post_icu_days_raw, 0)
  END                                                               AS post_icu_days,
  -- Two-part outcome: part 1 = any post-ICU ward stay (> 4 h); part 2 = days if > 4 h
  (b.post_icu_days_raw BETWEEN -4/24 AND 4/24)                      AS dc_directly_from_icu,
  (b.post_icu_days_raw > 4/24)                                      AS any_post_icu_stay,
  b.dc_group IN ('home', 'home_health', 'snf', 'rehab', 'ltach', 'other') AS dc_valid_primary,
  -- Sensitivity: drop AMA and acute-hospital transfers from 'other'
  (COALESCE(UPPER(b.discharge_location), '') = 'ACUTE HOSPITAL')    AS dc_acute_transfer
FROM base b;


-- ---------------------------------------------------------------------------
-- Step 2. Attrition (flow diagram) -- sequential exclusions, 18-64 track
-- ---------------------------------------------------------------------------
SELECT step, n FROM (
  SELECT 1 AS ord, 'Patients with >=1 ICU-containing admission (first such admission)' AS step,
         COUNT(*) AS n FROM `ctheg-scores.ins_icu.aim1_base`
  UNION ALL
  SELECT 2, 'Age 18-64 at that admission',
         COUNTIF(age_group = '18-64') FROM `ctheg-scores.ins_icu.aim1_base`
  UNION ALL
  SELECT 3, '... survived to hospital discharge',
         COUNTIF(age_group = '18-64' AND survived_to_dc) FROM `ctheg-scores.ins_icu.aim1_base`
  UNION ALL
  SELECT 4, '... admission-recorded Medicaid or Private',
         COUNTIF(age_group = '18-64' AND survived_to_dc AND ins_cat IN ('Medicaid', 'Private'))
         FROM `ctheg-scores.ins_icu.aim1_base`
  UNION ALL
  SELECT 5, '... valid discharge destination (excl. hospice/missing/unmapped)',
         COUNTIF(age_group = '18-64' AND survived_to_dc AND ins_cat IN ('Medicaid', 'Private')
                 AND dc_valid_primary)
         FROM `ctheg-scores.ins_icu.aim1_base`
  UNION ALL
  SELECT 6, '... valid ICU-exit-to-discharge timing (analytic cohort)',
         COUNTIF(age_group = '18-64' AND survived_to_dc AND ins_cat IN ('Medicaid', 'Private')
                 AND dc_valid_primary AND time_valid)
         FROM `ctheg-scores.ins_icu.aim1_base`
)
ORDER BY ord;

-- Excluded-group breakdowns for the flow diagram footnotes (18-64 survivors)
SELECT 'insurance' AS dim, ins_cat AS value, COUNT(*) AS n
FROM `ctheg-scores.ins_icu.aim1_base`
WHERE age_group = '18-64' AND survived_to_dc
GROUP BY ins_cat
UNION ALL
SELECT 'discharge', dc_group, COUNT(*)
FROM `ctheg-scores.ins_icu.aim1_base`
WHERE age_group = '18-64' AND survived_to_dc AND ins_cat IN ('Medicaid', 'Private')
GROUP BY dc_group
ORDER BY dim, n DESC;


-- ---------------------------------------------------------------------------
-- Step 3. Analytic views
-- ---------------------------------------------------------------------------
-- Primary (confirmatory): 18-64, Medicaid vs Private
CREATE OR REPLACE VIEW `ctheg-scores.ins_icu.aim1_primary_18_64` AS
SELECT
  *,
  (ins_cat = 'Medicaid') AS medicaid   -- reference = Private
FROM `ctheg-scores.ins_icu.aim1_base`
WHERE age_group = '18-64'
  AND survived_to_dc
  AND ins_cat IN ('Medicaid', 'Private')
  AND dc_valid_primary
  AND time_valid;

-- Separate analysis: 65+ survivors, all recorded insurance categories kept.
-- Do NOT label Medicaid-recorded 65+ patients as "dual eligible".
CREATE OR REPLACE VIEW `ctheg-scores.ins_icu.aim1_older_65plus` AS
SELECT *
FROM `ctheg-scores.ins_icu.aim1_base`
WHERE age_group = '65+'
  AND survived_to_dc
  AND ins_cat NOT IN ('Missing', 'UNMAPPED')
  AND dc_valid_primary
  AND time_valid;


-- ---------------------------------------------------------------------------
-- Step 4. Quick descriptive check of the primary cohort
-- ---------------------------------------------------------------------------
SELECT
  ins_cat,
  COUNT(*)                                                   AS n,
  ROUND(AVG(age_at_admit), 1)                                AS mean_age,
  ROUND(AVG(CAST(gender = 'F' AS INT64)), 3)                 AS prop_female,
  ROUND(AVG(CAST(dc_group = 'home' AS INT64)), 3)            AS p_home,
  ROUND(AVG(CAST(dc_group = 'home_health' AS INT64)), 3)     AS p_home_health,
  ROUND(AVG(CAST(dc_group = 'snf' AS INT64)), 3)             AS p_snf,
  ROUND(AVG(CAST(dc_group = 'rehab' AS INT64)), 3)           AS p_rehab,
  ROUND(AVG(CAST(dc_group = 'ltach' AS INT64)), 3)           AS p_ltach,
  ROUND(AVG(CAST(dc_group = 'other' AS INT64)), 3)           AS p_other,
  APPROX_QUANTILES(post_icu_days, 4)[OFFSET(1)]              AS post_icu_days_q1,
  APPROX_QUANTILES(post_icu_days, 4)[OFFSET(2)]              AS post_icu_days_median,
  APPROX_QUANTILES(post_icu_days, 4)[OFFSET(3)]              AS post_icu_days_q3,
  ROUND(AVG(CAST(dc_directly_from_icu AS INT64)), 3)         AS p_dc_from_icu,
  ROUND(AVG(CAST(dc_ama AS INT64)), 3)                       AS p_ama,
  ROUND(AVG(CAST(dc_acute_transfer AS INT64)), 3)            AS p_acute_transfer
FROM `ctheg-scores.ins_icu.aim1_primary_18_64`
GROUP BY ins_cat;
