-- =============================================================================
-- 00_value_counts.sql
-- Insurance and Post-ICU Transitions (MIMIC-IV v3.1) -- Aim 1 pre-checks
-- Run in BigQuery (billing project: ctheg-scores). Each query is independent;
-- run them one by one or as a script (results appear per statement).
-- Purpose: confirm the actual string values BEFORE fixing the CASE mappings
-- in 01_aim1_cohort.sql. Do not skip this -- v3.x changed the insurance field.
-- =============================================================================

-- Q1. Insurance values across ALL admissions (exact strings, incl. NULL/blank)
SELECT
  insurance                                   AS insurance_raw,
  COUNT(*)                                    AS n_admissions,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM `physionet-data.mimiciv_3_1_hosp.admissions`
GROUP BY insurance
ORDER BY n_admissions DESC;


-- Q2. Insurance values among ICU-containing admissions only
SELECT
  a.insurance                                 AS insurance_raw,
  COUNT(DISTINCT a.hadm_id)                   AS n_icu_admissions
FROM `physionet-data.mimiciv_3_1_hosp.admissions` a
WHERE a.hadm_id IN (SELECT hadm_id FROM `physionet-data.mimiciv_3_1_icu.icustays`)
GROUP BY a.insurance
ORDER BY n_icu_admissions DESC;


-- Q3. Discharge location values among ICU-containing admissions,
--     split by in-hospital death flag (checks NULL / DIED / HOSPICE handling)
SELECT
  a.discharge_location                        AS discharge_location_raw,
  a.hospital_expire_flag,
  COUNTIF(a.deathtime IS NOT NULL)            AS n_with_deathtime,
  COUNT(DISTINCT a.hadm_id)                   AS n_icu_admissions
FROM `physionet-data.mimiciv_3_1_hosp.admissions` a
WHERE a.hadm_id IN (SELECT hadm_id FROM `physionet-data.mimiciv_3_1_icu.icustays`)
GROUP BY a.discharge_location, a.hospital_expire_flag
ORDER BY a.hospital_expire_flag, n_icu_admissions DESC;


-- Q4. Insurance x anchor_year_group (is "Private" recorded consistently
--     across eras? If one era lacks it, era adjustment/restriction is needed)
SELECT
  p.anchor_year_group,
  a.insurance                                 AS insurance_raw,
  COUNT(DISTINCT a.hadm_id)                   AS n_icu_admissions
FROM `physionet-data.mimiciv_3_1_hosp.admissions` a
JOIN `physionet-data.mimiciv_3_1_hosp.patients` p USING (subject_id)
WHERE a.hadm_id IN (SELECT hadm_id FROM `physionet-data.mimiciv_3_1_icu.icustays`)
GROUP BY p.anchor_year_group, a.insurance
ORDER BY p.anchor_year_group, n_icu_admissions DESC;


-- Q5. Insurance x age group at admission (expect Medicare < 65 to be small:
--     disability/ESRD entitlement -- excluded from the Medicaid-vs-private
--     contrast but reported in the attrition table)
SELECT
  CASE
    WHEN p.anchor_age + (EXTRACT(YEAR FROM a.admittime) - p.anchor_year) < 18 THEN '<18'
    WHEN p.anchor_age + (EXTRACT(YEAR FROM a.admittime) - p.anchor_year) <= 64 THEN '18-64'
    ELSE '65+'
  END                                         AS age_group,
  a.insurance                                 AS insurance_raw,
  COUNT(DISTINCT a.hadm_id)                   AS n_icu_admissions
FROM `physionet-data.mimiciv_3_1_hosp.admissions` a
JOIN `physionet-data.mimiciv_3_1_hosp.patients` p USING (subject_id)
WHERE a.hadm_id IN (SELECT hadm_id FROM `physionet-data.mimiciv_3_1_icu.icustays`)
GROUP BY age_group, a.insurance
ORDER BY age_group, n_icu_admissions DESC;


-- Q6. Timing sanity check: final ICU outtime vs hospital dischtime
--     (negative gaps = outtime recorded after discharge; must be handled)
WITH last_icu AS (
  SELECT hadm_id, MAX(outtime) AS last_icu_outtime
  FROM `physionet-data.mimiciv_3_1_icu.icustays`
  GROUP BY hadm_id
)
SELECT
  COUNT(*)                                                        AS n_icu_admissions,
  COUNTIF(l.last_icu_outtime IS NULL)                             AS n_null_outtime,
  COUNTIF(DATETIME_DIFF(a.dischtime, l.last_icu_outtime, MINUTE) < 0)    AS n_negative_gap,
  COUNTIF(DATETIME_DIFF(a.dischtime, l.last_icu_outtime, MINUTE) BETWEEN 0 AND 240) AS n_gap_0_to_4h,
  APPROX_QUANTILES(DATETIME_DIFF(a.dischtime, l.last_icu_outtime, HOUR) / 24.0, 100)[OFFSET(50)] AS median_days,
  APPROX_QUANTILES(DATETIME_DIFF(a.dischtime, l.last_icu_outtime, HOUR) / 24.0, 100)[OFFSET(99)] AS p99_days
FROM `physionet-data.mimiciv_3_1_hosp.admissions` a
JOIN last_icu l USING (hadm_id);
