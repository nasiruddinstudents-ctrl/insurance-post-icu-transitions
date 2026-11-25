# Insurance and post-acute care after ICU-level hospitalization

Analysis code for:

> Uddin MN. *Medicaid and Discharge to Inpatient Rehabilitation Versus Skilled Nursing Care Among Working-Age Survivors of Critical Illness: A Two-Setting Cohort Study.* Manuscript under review, 2026.
>
> Preprint (earlier version): Uddin MN. medRxiv 2026. https://doi.org/10.64898/2026.11.13.26368054

Release **v1.1** is the code for the submitted manuscript. Release v1.0 accompanies the preprint; the analysis code is unchanged between the two releases.

The code describes Medicaid–private differences in discharge destination and length of stay in two settings:

- **Setting 1:** working-age survivors of a first ICU admission at one Massachusetts academic medical center (MIMIC-IV v3.1, 2008–2022).
- **Setting 2:** Texas hospitalizations with ICU or CCU accommodation charges (Texas Inpatient Public Use Data File, 2017–2020).

**This repository contains code only — no data and no outputs.** Both data sources are restricted by data use agreements; obtain them from their custodians (below).

## Repository layout

```
mimic/
  sql/        BigQuery SQL: value counts, cohort, covariates, social-needs flags
  notebooks/  Python (Colab) analysis notebooks, outputs cleared
texas/        Python scripts for the Texas PUDF (run locally) + synthetic-data test
```

### Setting 1: MIMIC-IV (Google BigQuery + Colab)

1. Obtain credentialed access to MIMIC-IV v3.1 on PhysioNet (https://physionet.org) and link it to a Google Cloud project.
2. In BigQuery, run `mimic/sql/01_aim1_cohort.sql`, then `02_aim1_covariates.sql`, then `03_aim1_s4_flags.sql`. Replace the project name `ctheg-scores` with your own.
3. Run the notebooks in this order:

   | Notebook | Contents |
   |---|---|
   | `aim1_models_FINAL.ipynb` | Primary models, bootstrap, IPW, sensitivity analyses, age 65+ |
   | `aim1_s4_evalues.ipynb` | Social-needs adjustment and E-values |
   | `mimic_bridge.ipynb` | Texas-comparable definitions (harmonized comparison) |
   | `mimic_revision.ipynb` | Sequential adjustment (M0–M4) and IPW diagnostics |
   | `mimic_period.ipynb` | Admission-time (M0) standardized estimates and the 2017–2019 common-period comparison |

### Setting 2: Texas Inpatient PUDF (local computer)

See `texas/README.md` for setup. In brief:

```
python texas/00_discover.py  /path/to/PUDF_folder
python texas/01_build_cohort.py /path/to/PUDF_folder 2017 2020
python texas/02_analysis.py --boot 100
python texas/03_revision.py
python texas/04_bounded.py
```

`02_analysis.py` takes `--boot N` for the hospital-cluster bootstrap of the multinomial check (100 replicates in the paper).

To check the pipeline without real data, generate fake files in the PUDF layout with `python texas/test/make_synthetic.py /tmp/synth`.

## Where each result comes from

Table and figure numbers refer to the submitted manuscript and its supplement (Supplementary Table S21 gives the same map with seeds).

| Result | Code | Output (aggregate) |
|---|---|---|
| Setting 1 cohort, Table 1, M3 destinations and post-ICU days (Table 2; Tables S14–S16) | `mimic/notebooks/aim1_models_FINAL.ipynb` | `aim1_results.xlsx` |
| Setting 1 harmonized estimates (Table 3 A–B; Figure 2) | `mimic/notebooks/mimic_bridge.ipynb` | `mimic_bridge_results.xlsx` |
| Setting 1 sequential models and IPW diagnostics (Table 5; Tables S5, S10) | `mimic/notebooks/mimic_revision.ipynb` | `mimic_revision_results.xlsx` |
| Setting 1 M0 standardized estimates and 2017–2019 subset (Tables 2–3 C; Table S20) | `mimic/notebooks/mimic_period.ipynb` | `mimic_period_results.xlsx` |
| Setting 1 social-needs adjustment and E-values | `mimic/notebooks/aim1_s4_evalues.ipynb` | — |
| Setting 2 cohort build (all Setting 2 results) | `texas/01_build_cohort.py` | `tx_build_log.txt` |
| Setting 2 flow, Table 1, linear models, sensitivity analyses S1–S11 (Tables 1, 4; Tables S1, S3, S4, S11) | `texas/02_analysis.py` | `tx_results.xlsx` |
| Setting 2 sequential models, identification, influence, ICU mix, missing data, multinomial (Table 5; Tables S5–S9) | `texas/03_revision.py` | `tx_revision.xlsx` |
| Setting 2 fixed-effects logistic and Poisson models, calibration, sub-cohorts, present-on-admission Charlson, E-values (Tables 2–3; Tables S1, S17–S20) | `texas/04_bounded.py` | `tx_bounded.xlsx` |

Seeds: 20261112 (Setting 1 primary bootstrap, B = 500) and 20261113 (Setting 1 common-period bootstrap and Setting 2 cluster bootstrap).

## Data access

- **MIMIC-IV v3.1:** Johnson A, et al. PhysioNet, 2024. https://doi.org/10.13026/kpb9-mt58 (credentialed access).
- **Texas Hospital Inpatient Discharge Public Use Data File:** Texas Department of State Health Services, Center for Health Statistics, Austin, Texas (data use agreement).
  - The analyses, interpretations, and conclusions are the author's and not those of the Department.

## Privacy

- Record-level data never leave the analyst's environment.
- Exported tables are aggregate, and cells with fewer than 11 patients or stays are suppressed.
- Do not commit data files or outputs (see `.gitignore`).

## Software

- Setting 2 (run locally on macOS): Python 3.12.3, pandas 3.1.1, NumPy 2.5.4, statsmodels 0.15.0, SciPy 1.18.1, openpyxl.
- Setting 1 (Google Colaboratory): pandas 2.2.3, NumPy 2.4.6, statsmodels 0.15.0; BigQuery views from the SQL in `mimic/sql`.

## License

MIT (code only).
