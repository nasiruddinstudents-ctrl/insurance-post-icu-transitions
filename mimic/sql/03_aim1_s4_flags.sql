-- =============================================================================
-- 03_aim1_s4_flags.sql
-- Insurance and Post-ICU Transitions (MIMIC-IV v3.1) -- Sensitivity model S4
-- ICD-coded social-needs proxies for every admission in aim1_analytic.
-- Adapted from the Elicit template (aim1_s4_social_needs.sql); code groups unchanged.
--
-- These are discharge-coded proxies, not verified baseline social needs:
--   housing_econ_icd : ICD-10 Z59.x / ICD-9 V60.x (housing, household, economic circumstances)
--   sud_icd          : ICD-10 F10-F16, F18, F19 (excl. F17 nicotine) / ICD-9 291, 292, 303, 304,
--                      305 excl. 305.1 (tobacco)
--   smi_icd          : ICD-10 F20-F29, F30, F31, F32.2-F32.3, F33.2-F33.3 /
--                      ICD-9 295, 296.0-296.1, 296.4-296.8, 296.23-296.24, 296.33-296.34
--                      (note: ICD-9 297-298 other psychoses not included; ICD-10 F22-F29 are)
-- Output: ctheg-scores.ins_icu.aim1_s4_flags (one row per hadm_id). Do not export row-level data.
-- =============================================================================

CREATE OR REPLACE TABLE `ctheg-scores.ins_icu.aim1_s4_flags` AS
WITH cohort AS (
  SELECT DISTINCT hadm_id FROM `ctheg-scores.ins_icu.aim1_analytic`
),
codes AS (
  SELECT d.hadm_id, d.icd_version, UPPER(REGEXP_REPLACE(d.icd_code, r'\.', '')) AS code
  FROM `physionet-data.mimiciv_3_1_hosp.diagnoses_icd` d
  JOIN cohort USING (hadm_id)
),
flags AS (
  SELECT
    hadm_id,
    LOGICAL_OR((icd_version = 10 AND STARTS_WITH(code, 'Z59'))
            OR (icd_version = 9  AND STARTS_WITH(code, 'V60')))                           AS housing_econ_icd,
    LOGICAL_OR((icd_version = 10 AND REGEXP_CONTAINS(code, r'^F(1[0-6]|18|19)'))
            OR (icd_version = 9  AND (REGEXP_CONTAINS(code, r'^(291|292|303|304)')
                                      OR (STARTS_WITH(code, '305') AND NOT STARTS_WITH(code, '3051'))))) AS sud_icd,
    LOGICAL_OR((icd_version = 10 AND REGEXP_CONTAINS(code, r'^(F2|F30|F31|F32[23]|F33[23])'))
            OR (icd_version = 9  AND REGEXP_CONTAINS(code, r'^(295|296[0145678]|2962[34]|2963[34])'))) AS smi_icd
  FROM codes
  GROUP BY hadm_id
)
SELECT
  c.hadm_id,
  COALESCE(f.housing_econ_icd, FALSE) AS housing_econ_icd,
  COALESCE(f.sud_icd, FALSE)          AS sud_icd,
  COALESCE(f.smi_icd, FALSE)          AS smi_icd
FROM cohort c
LEFT JOIN flags f USING (hadm_id);


-- QC: prevalence by insurance and admission period in the primary cohort
-- (aggregate only; safe to share). Expect each flag to be more common among Medicaid-recorded
-- patients; housing/economic codes are often rare, especially in earlier (ICD-9) years.
SELECT
  v.anchor_year_group,
  v.ins_cat,
  COUNT(*)                                                AS n,
  ROUND(100 * AVG(CAST(s.housing_econ_icd AS INT64)), 1)  AS pct_housing_econ,
  ROUND(100 * AVG(CAST(s.sud_icd AS INT64)), 1)           AS pct_sud,
  ROUND(100 * AVG(CAST(s.smi_icd AS INT64)), 1)           AS pct_smi
FROM `ctheg-scores.ins_icu.aim1_primary_18_64` v
JOIN `ctheg-scores.ins_icu.aim1_s4_flags` s USING (hadm_id)
GROUP BY ROLLUP (v.ins_cat, v.anchor_year_group)
ORDER BY v.ins_cat, v.anchor_year_group;
