-- =============================================================================
-- 02_aim1_covariates.sql
-- Insurance and Post-ICU Transitions (MIMIC-IV v3.1) -- Aim 1 covariates
-- Input : ctheg-scores.ins_icu.aim1_base  (from 01_aim1_cohort.sql)
-- Output: ctheg-scores.ins_icu.aim1_analytic + rebuilt views
--
-- Covariate definitions (for the methods section):
--  * SOFA      = first-day SOFA of the FIRST ICU stay of the admission
--                (mimiciv_derived.first_day_sofa).
--  * Charlson  = Charlson Comorbidity Index from that admission's ICD codes
--                (mimiciv_derived.charlson). The derived total includes an
--                age component; cci_no_age removes it because age is adjusted
--                separately. Note: ICD codes are assigned at discharge and can
--                include in-hospital complications (limitation).
--  * Invasive ventilation = any 'InvasiveVent' or 'Tracheostomy' episode in
--                any ICU stay of the admission (mimiciv_derived.ventilation).
--  * Vasopressors = any norepinephrine, epinephrine, dopamine, phenylephrine
--                or vasopressin in any ICU stay of the admission
--                (mimiciv_derived.vasoactive_agent). Inotropes only
--                (dobutamine, milrinone) are NOT counted.
--  * ICU LOS   = icu_los_days_total (already in aim1_base).
--  * Race, admission type: grouped below; unmatched strings -> 'UNMAPPED'.
--  * Era       = anchor_year_group (3-year window; approximate by design).
--
-- Derived dataset confirmed as mimiciv_3_1_derived (Nov 12, 2026).
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Step 1. Analytic table = base cohort + covariates (all ages, all flags kept)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE `ctheg-scores.ins_icu.aim1_analytic` AS
WITH stays AS (               -- all ICU stays belonging to cohort admissions
  SELECT s.hadm_id, s.stay_id
  FROM `physionet-data.mimiciv_3_1_icu.icustays` s
  WHERE s.hadm_id IN (SELECT hadm_id FROM `ctheg-scores.ins_icu.aim1_base`)
),

sofa AS (
  SELECT stay_id, sofa AS sofa_day1
  FROM `physionet-data.mimiciv_3_1_derived.first_day_sofa`
),

cci AS (
  SELECT
    hadm_id,
    charlson_comorbidity_index                      AS cci_total,
    charlson_comorbidity_index - age_score          AS cci_no_age
  FROM `physionet-data.mimiciv_3_1_derived.charlson`
),

vent AS (
  SELECT
    st.hadm_id,
    LOGICAL_OR(v.ventilation_status IN ('InvasiveVent', 'Tracheostomy')) AS invasive_vent,
    SUM(CASE WHEN v.ventilation_status IN ('InvasiveVent', 'Tracheostomy')
             THEN DATETIME_DIFF(v.endtime, v.starttime, MINUTE) / 1440.0
             ELSE 0 END)                                                 AS invasive_vent_days
  FROM stays st
  JOIN `physionet-data.mimiciv_3_1_derived.ventilation` v USING (stay_id)
  GROUP BY st.hadm_id
),

pressor AS (
  SELECT
    st.hadm_id,
    LOGICAL_OR(COALESCE(va.norepinephrine, va.epinephrine, va.dopamine,
                        va.phenylephrine, va.vasopressin) IS NOT NULL)   AS vasopressor
  FROM stays st
  JOIN `physionet-data.mimiciv_3_1_derived.vasoactive_agent` va USING (stay_id)
  GROUP BY st.hadm_id
)

SELECT
  b.*,

  -- Severity / comorbidity ----------------------------------------------------
  so.sofa_day1,
  c.cci_total,
  c.cci_no_age,

  -- Organ support (absence of a record = not received) ----------------------
  COALESCE(v.invasive_vent, FALSE)                  AS invasive_vent,
  COALESCE(v.invasive_vent_days, 0)                 AS invasive_vent_days,
  COALESCE(p.vasopressor, FALSE)                    AS vasopressor,

  -- Race (grouped) ------------------------------------------------------------
  CASE
    WHEN b.race IS NULL                                                   THEN 'Unknown'
    WHEN UPPER(b.race) LIKE 'HISPANIC%' OR UPPER(b.race) = 'SOUTH AMERICAN' THEN 'Hispanic/Latino'
    WHEN UPPER(b.race) LIKE 'BLACK%'                                      THEN 'Black'
    WHEN UPPER(b.race) LIKE 'WHITE%' OR UPPER(b.race) = 'PORTUGUESE'      THEN 'White'
    WHEN UPPER(b.race) LIKE 'ASIAN%'                                      THEN 'Asian'
    WHEN UPPER(b.race) IN ('UNKNOWN', 'UNABLE TO OBTAIN',
                           'PATIENT DECLINED TO ANSWER')                  THEN 'Unknown'
    WHEN UPPER(b.race) LIKE 'AMERICAN INDIAN%'
      OR UPPER(b.race) LIKE 'NATIVE HAWAIIAN%'
      OR UPPER(b.race) LIKE 'MULTIPLE%'
      OR UPPER(b.race) = 'OTHER'                                          THEN 'Other'
    ELSE 'UNMAPPED'
  END                                               AS race_cat,

  -- Admission type (grouped) --------------------------------------------------
  CASE
    WHEN UPPER(b.admission_type) IN ('EW EMER.', 'DIRECT EMER.')          THEN 'Emergency'
    WHEN UPPER(b.admission_type) = 'URGENT'                               THEN 'Urgent'
    WHEN UPPER(b.admission_type) IN ('ELECTIVE',
                                     'SURGICAL SAME DAY ADMISSION')       THEN 'Elective/Same-day surgical'
    WHEN UPPER(b.admission_type) LIKE '%OBSERVATION%'                     THEN 'Observation'
    ELSE 'UNMAPPED'
  END                                               AS adm_type_cat,

  -- Language (descriptive / possible sensitivity covariate) -------------------
  CASE
    WHEN b.language IS NULL OR UPPER(b.language) IN ('?', 'UNKNOWN') THEN 'Unknown'
    WHEN UPPER(b.language) = 'ENGLISH'                               THEN 'English'
    ELSE 'Non-English'
  END                                               AS language_cat,

  -- Coverage flags for QC ------------------------------------------------------
  (so.stay_id IS NOT NULL)                          AS has_sofa,
  (c.hadm_id IS NOT NULL)                           AS has_cci

FROM `ctheg-scores.ins_icu.aim1_base` b
LEFT JOIN sofa    so ON so.stay_id = b.first_stay_id
LEFT JOIN cci     c  ON c.hadm_id  = b.hadm_id
LEFT JOIN vent    v  ON v.hadm_id  = b.hadm_id
LEFT JOIN pressor p  ON p.hadm_id  = b.hadm_id;


-- ---------------------------------------------------------------------------
-- Step 2. Rebuild the analysis views on the analytic table
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `ctheg-scores.ins_icu.aim1_primary_18_64` AS
SELECT
  *,
  (ins_cat = 'Medicaid') AS medicaid   -- reference = Private
FROM `ctheg-scores.ins_icu.aim1_analytic`
WHERE age_group = '18-64'
  AND survived_to_dc
  AND ins_cat IN ('Medicaid', 'Private')
  AND dc_valid_primary
  AND time_valid;

CREATE OR REPLACE VIEW `ctheg-scores.ins_icu.aim1_older_65plus` AS
SELECT *
FROM `ctheg-scores.ins_icu.aim1_analytic`
WHERE age_group = '65+'
  AND survived_to_dc
  AND ins_cat NOT IN ('Missing', 'UNMAPPED')
  AND dc_valid_primary
  AND time_valid;


-- ---------------------------------------------------------------------------
-- Step 3. QC: derived-table coverage and unmapped categories (primary cohort)
-- ---------------------------------------------------------------------------
-- Expect has_sofa and has_cci near 100%. Much lower = derived tables were
-- built on an older MIMIC version; tell me before modelling.
SELECT
  ins_cat,
  COUNT(*)                                                   AS n,
  ROUND(AVG(CAST(has_sofa AS INT64)), 4)                     AS p_has_sofa,
  ROUND(AVG(CAST(has_cci  AS INT64)), 4)                     AS p_has_cci,
  COUNTIF(race_cat = 'UNMAPPED')                             AS n_race_unmapped,
  COUNTIF(adm_type_cat = 'UNMAPPED')                         AS n_admtype_unmapped
FROM `ctheg-scores.ins_icu.aim1_primary_18_64`
GROUP BY ins_cat;

-- Raw strings that fell into UNMAPPED (should return 0 rows)
SELECT 'race' AS field, race AS raw_value, COUNT(*) AS n
FROM `ctheg-scores.ins_icu.aim1_analytic`
WHERE race_cat = 'UNMAPPED' GROUP BY race
UNION ALL
SELECT 'admission_type', admission_type, COUNT(*)
FROM `ctheg-scores.ins_icu.aim1_analytic`
WHERE adm_type_cat = 'UNMAPPED' GROUP BY admission_type
ORDER BY field, n DESC;


-- ---------------------------------------------------------------------------
-- Step 4. Table 1 (continuous + binary covariates), primary cohort
-- ---------------------------------------------------------------------------
SELECT
  ins_cat,
  COUNT(*)                                                   AS n,
  ROUND(AVG(age_at_admit), 1)                                AS age_mean,
  ROUND(STDDEV(age_at_admit), 1)                             AS age_sd,
  ROUND(AVG(CAST(gender = 'F' AS INT64)), 3)                 AS p_female,
  APPROX_QUANTILES(sofa_day1, 4)[OFFSET(2)]                  AS sofa_median,
  APPROX_QUANTILES(sofa_day1, 4)[OFFSET(1)]                  AS sofa_q1,
  APPROX_QUANTILES(sofa_day1, 4)[OFFSET(3)]                  AS sofa_q3,
  APPROX_QUANTILES(cci_no_age, 4)[OFFSET(2)]                 AS cci_no_age_median,
  APPROX_QUANTILES(cci_no_age, 4)[OFFSET(1)]                 AS cci_no_age_q1,
  APPROX_QUANTILES(cci_no_age, 4)[OFFSET(3)]                 AS cci_no_age_q3,
  ROUND(AVG(CAST(invasive_vent AS INT64)), 3)                AS p_invasive_vent,
  ROUND(AVG(CAST(vasopressor AS INT64)), 3)                  AS p_vasopressor,
  APPROX_QUANTILES(icu_los_days_total, 4)[OFFSET(2)]         AS icu_los_median,
  APPROX_QUANTILES(icu_los_days_total, 4)[OFFSET(1)]         AS icu_los_q1,
  APPROX_QUANTILES(icu_los_days_total, 4)[OFFSET(3)]         AS icu_los_q3,
  ROUND(AVG(CAST(n_icu_stays > 1 AS INT64)), 3)              AS p_icu_readmit_same_hosp
FROM `ctheg-scores.ins_icu.aim1_primary_18_64`
GROUP BY ins_cat;

-- Table 1 (categorical covariates), primary cohort: % within insurance group
SELECT var, level, ins_cat, n,
       ROUND(100 * n / SUM(n) OVER (PARTITION BY var, ins_cat), 1) AS pct
FROM (
  SELECT 'race' AS var, race_cat AS level, ins_cat, COUNT(*) AS n
  FROM `ctheg-scores.ins_icu.aim1_primary_18_64` GROUP BY race_cat, ins_cat
  UNION ALL
  SELECT 'admission_type', adm_type_cat, ins_cat, COUNT(*)
  FROM `ctheg-scores.ins_icu.aim1_primary_18_64` GROUP BY adm_type_cat, ins_cat
  UNION ALL
  SELECT 'first_careunit', first_careunit, ins_cat, COUNT(*)
  FROM `ctheg-scores.ins_icu.aim1_primary_18_64` GROUP BY first_careunit, ins_cat
  UNION ALL
  SELECT 'era', anchor_year_group, ins_cat, COUNT(*)
  FROM `ctheg-scores.ins_icu.aim1_primary_18_64` GROUP BY anchor_year_group, ins_cat
  UNION ALL
  SELECT 'language', language_cat, ins_cat, COUNT(*)
  FROM `ctheg-scores.ins_icu.aim1_primary_18_64` GROUP BY language_cat, ins_cat
)
ORDER BY var, level, ins_cat;
