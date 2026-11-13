# Insurance and post-acute care after ICU-level hospitalization

Analysis code for:

> Uddin MN. *Medicaid and the Type of Post-Acute Care After ICU-Level Hospitalization: A Two-Setting Comparison of a Massachusetts Academic Medical Center and Texas Hospital Discharge Data.* Manuscript under review, 2026.

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
   | `mimic_revision.ipynb` | Sequential adjustment and IPW diagnostics |

### Setting 2: Texas Inpatient PUDF (local computer)

See `texas/README.md` for setup. In brief:

```
python texas/00_discover.py  /path/to/PUDF_folder
python texas/01_build_cohort.py /path/to/PUDF_folder 2017 2020
python texas/02_analysis.py --boot 100
python texas/03_revision.py
```

To check the pipeline without real data, generate fake files in the PUDF layout with `python texas/test/make_synthetic.py /tmp/synth`.

## Data access

- **MIMIC-IV v3.1:** Johnson A, et al. PhysioNet, 2024. https://doi.org/10.13026/kpb9-mt58 (credentialed access).
- **Texas Hospital Inpatient Discharge Public Use Data File:** Texas Department of State Health Services, Center for Health Statistics, Austin, Texas (data use agreement).
  - The analyses, interpretations, and conclusions are the author's and not those of the Department.

## Privacy

- Record-level data never leave the analyst's environment.
- Exported tables are aggregate, and cells with fewer than 11 patients or stays are suppressed.
- Do not commit data files or outputs (see `.gitignore`).

## Software

Python 3.12–3.13, pandas 2.2+, numpy, statsmodels 0.14+, scipy, openpyxl, patsy; Google BigQuery.

## License

MIT (code only).
