# Texas Inpatient PUDF replication: insurance and discharge after ICU-level care

This is the external replication of the MIMIC-IV analysis, using the Texas Hospital Inpatient Discharge Public Use Data File (PUDF) for 2017–2020.

- **Primary years:** 2017–2019.
- **Sensitivity years:** 2020 alone, and 2017–2020 pooled.

**Data Use Agreement.** Keep the PUDF files and `tx_output/tx_cohort.pkl` on your Mac. Do not upload them, email them, put them in cloud folders or GitHub, or paste records into any chat.

Only these two files are meant to be shared: `tx_output/tx_results_summary.txt` and `tx_output/tx_results.xlsx`. Both are aggregate, and any cell under 11 is suppressed.

## One-time setup on the Mac (about 10 minutes)

1. Open **Terminal** (Spotlight → "Terminal").
2. Create a Python environment.

   If you have Anaconda or Miniconda:
   ```
   conda create -n txpudf python=3.12 pandas numpy statsmodels openpyxl -y
   conda activate txpudf
   ```
   Without conda (macOS's own Python 3):
   ```
   python3 -m venv ~/txpudf
   source ~/txpudf/bin/activate
   pip install pandas numpy statsmodels openpyxl
   ```
3. Unzip this code folder somewhere outside the data folder, for example `~/Documents/tx_replication`.

## Every session

```
conda activate txpudf          # or: source ~/txpudf/bin/activate
cd ~/Documents/tx_replication
```
Tip: type `cd ` and then drag the folder from Finder into Terminal to paste its path.

## Steps

| Step | Command | Time | Share? |
|---|---|---|---|
| 1. Check files | `python 00_discover.py "/path/to/Texas PUDF folder"` | seconds | yes, the whole output |
| 2. Build cohort | `python 01_build_cohort.py "/path/to/Texas PUDF folder" 2017 2020` | 15–40 min | only `tx_output/tx_build_log.txt` |
| 3. Analysis | `python 02_analysis.py` | a few minutes | `tx_results_summary.txt`, `tx_results.xlsx` |
| 4. Bootstrap check | `python 02_analysis.py --boot 100` | about half of a 200-replicate run (1–3 hours) | same two files |
| 5. Sequential models and diagnostics | `python 03_revision.py` | varies | `tx_output/tx_revision.xlsx` |
| 6. Fixed-effects logistic and Poisson models, calibration, sub-cohorts | `python 04_bounded.py` | varies | `tx_output/tx_bounded.xlsx` |

Zipped downloads can be left zipped; the scripts read inside the zip files. Include 2021 Q1 if you have it, because late 2020 discharges can appear in the next quarter's file.

## Definitions (all from the 2019 PUDF User Manual)

- **ICU-level stay:** ICU or CCU accommodation charges greater than zero. These are `ICU_AMOUNT` (revenue center 020X) or `CCU_AMOUNT` (021X) in Base Data #2.
  - S2 instead uses specialty-unit codes I or C in `SPEC_UNIT_1–5`.
- **Unit of analysis:** the hospitalization. The PUDF has no patient identifier, so patients with several stays contribute several records.
- **Age:** `PAT_AGE` codes 06–15, plus 23–24. Codes 23–24 are the coarse age groups that DSHS uses on HIV and substance-use records.
- **Insurance:** `FIRST_PAYMENT_SRC`, the expected primary payer recorded on the claim.
  - Medicaid = MC.
  - Private = 12, 13, 14, 15, HM, BL, CI.
  - Medicare = MA, MB, 16 (16 is Medicare-risk HMO).
  - Medicaid managed-care claims coded as commercial HMO cannot be separated out.
- **Destination:** `PAT_STATUS`. The planned-readmission variants (81–95) are folded into their base codes.
  - home: 01
  - home health: 06
  - SNF/NF: 03, 61, 64
  - IRF: 62
  - LTCH: 63
  - other: AMA, acute transfers, psychiatric, custodial, court and other codes
  - excluded: death (20, 40–42), hospice (50–51), still a patient (30, 09)
- **Length of stay:** `LENGTH_OF_STAY`, in whole days with a minimum of 1. This covers the whole stay; the PUDF has no ICU discharge time, so post-ICU days cannot be computed.
- **Hospitals excluded:**
  - masked IDs (999999 and 999998), which cannot serve as fixed effects;
  - specialty hospitals with no acute-care indicator.
- **Covariates:**
  - patient: age group, sex, race, ethnicity, admission type and source, emergency-department flag;
  - severity: APR-DRG severity of illness, risk of mortality and MDC; Charlson index without age points (Quan ICD-10 codes);
  - ventilation: any invasive ventilation, and ventilation of 96 hours or more;
  - fixed effects: discharge quarter.

## Models

- **Primary destination estimates (`04_bounded.py`):** logistic regression with a fixed effect for every hospital, standardized to the analytic sample (marginal standardization), with delta-method CIs from a hospital-clustered variance. Poisson models with hospital fixed effects give conditional risk ratios.
- **Length of stay and sensitivity analyses (`02_analysis.py`, `03_revision.py`):** linear models with hospital and quarter fixed effects; standard errors clustered by hospital.
- **Covariate sets:** M0 (admission-time) through M4 (adds social-needs codes), added in temporal order; M0 is the primary comparison.

The Medicaid coefficient is the adjusted difference from private insurance within the same hospital, in percentage points.

Robustness checks:
- the same covariates without hospital fixed effects;
- a multinomial logit with g-computation, the method used in the MIMIC paper;
- E-values;
- sensitivity analyses S1–S10;
- teaching and ventilation subgroups;
- age 65+ (Medicaid vs Medicare as primary payer).

## Required citation (Data Use Agreement)

Texas Hospital Inpatient Discharge Public Use Data File, [quarter and year of data]. Texas Department of State Health Services, Center for Health Statistics, Austin, Texas. [date of publication].

The paper must also state that the interpretations are the author's, not DSHS's.

## Testing

`python test/make_synthetic.py /tmp/synth` writes fake files in the PUDF layout. You can run steps 1–3 on them to check the setup without real data.
