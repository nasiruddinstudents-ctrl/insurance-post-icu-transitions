# %% [markdown]
# # MIMIC-IV bridge analysis for the Texas replication
# Paste these cells at the END of `aim1_models_FINAL.ipynb` and run them after the setup,
# LOAD and helper cells have run (they reuse `client`, `PROJECT`, `prep`).
# They re-estimate the MIMIC Medicaid-private gap with the outcome definitions the Texas
# PUDF allows: whole-stay length of stay in calendar days (discharge date minus admission
# date, minimum 1) instead of post-ICU days, linear probability models, and a covariate set
# limited to what the Texas file also has. Output is aggregate only.

# %%
import warnings, numpy as np, pandas as pd, statsmodels.formula.api as smf
warnings.filterwarnings("ignore")   # the vent term is constant inside the ventilation cohort

rawB = client.query(f"SELECT * FROM `{PROJECT}.ins_icu.aim1_primary_18_64`").to_dataframe()
dB = prep(rawB, ["Private", "Medicaid"])
dB["medicaid"] = (dB["ins_cat"] == "Medicaid").astype(int)
adm = pd.to_datetime(dB["admittime"]).dt.normalize()
dis = pd.to_datetime(dB["dischtime"]).dt.normalize()
dB["los_tx"] = ((dis - adm).dt.days).clip(lower=1).astype(float)      # Texas LENGTH_OF_STAY rule
dB["y_snf"] = (dB["dc_group"] == "snf").astype(float) * 100
dB["y_rehab"] = (dB["dc_group"] == "rehab").astype(float) * 100
dB["y_facility"] = dB["dc_group"].isin(["snf", "rehab", "ltach"]).astype(float) * 100
dB["y_home"] = (dB["dc_group"] == "home").astype(float) * 100
dB["cci_cat"] = pd.cut(dB["cci_no_age"], [-1, 0, 1, 2, 4, 99], labels=["0", "1", "2", "3-4", "5+"]).astype(str)
dB["age_band"] = pd.cut(dB["age_at_admit"], [17, 24, 34, 44, 54, 64]).astype(str)   # Texas age groups
print(f"n = {len(dB):,}; Medicaid {dB['medicaid'].sum():,}")

COV_FULL = COV                                             # the paper's covariate set
COV_TX = "C(age_band) + female + C(race_cat) + C(adm_type_cat) + C(cci_cat) + vent + C(anchor_year_group)"

rows = []
for cohort, sub in [("All ICU survivors 18-64", dB), ("Invasive ventilation", dB[dB["vent"] == 1])]:
    for spec, cov in [("Paper covariates", COV_FULL), ("Texas-comparable covariates", COV_TX)]:
        for y in ["y_snf", "y_rehab", "y_facility", "y_home", "los_tx"]:
            r = smf.ols(f"{y} ~ medicaid + {cov}", data=sub).fit(cov_type="HC1")
            b, se = r.params["medicaid"], r.bse["medicaid"]
            rows.append(dict(cohort=cohort, covariates=spec, outcome=y, n=int(r.nobs),
                             private_mean=sub.loc[sub.medicaid == 0, y].mean(),
                             medicaid_mean=sub.loc[sub.medicaid == 1, y].mean(),
                             diff=b, lo=b - 1.96 * se, hi=b + 1.96 * se))
bridge = pd.DataFrame(rows)
print(bridge.round(2).to_string(index=False))
bridge.to_excel("mimic_bridge_results.xlsx", index=False)      # aggregate only; download and share
