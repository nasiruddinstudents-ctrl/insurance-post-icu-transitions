"""
02_analysis.py - Texas PUDF replication: insurance and discharge destination / length of stay
after ICU-level hospital care (adults 18-64, Medicaid vs private, survivors).

Run:   python 02_analysis.py            (uses tx_output/tx_cohort.pkl from 01_build_cohort.py)
       python 02_analysis.py --boot 200 (adds hospital-cluster bootstrap CIs to the
                                          multinomial-logit robustness check; slow)

Primary model: linear probability models with hospital fixed effects and discharge-quarter
fixed effects, standard errors clustered by hospital. The Medicaid coefficient is the
within-hospital adjusted percentage-point difference vs private insurance.

Writes tx_output/tx_results.xlsx and tx_output/tx_results_summary.txt. Both are aggregate
(cells < 11 suppressed) and are the only outputs meant to leave this computer.
"""
import argparse
import os
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm

import tx_common as C

warnings.filterwarnings("ignore")
ap = argparse.ArgumentParser()
ap.add_argument("--boot", type=int, default=0, help="bootstrap reps for the MNLogit check")
ap.add_argument("--main-years", default="2017-2019")
args = ap.parse_args()
Y0, Y1 = map(int, args.main_years.split("-"))
OUT = "tx_output"
rng = np.random.default_rng(20261113)
summary = []


def say(x=""):
    print(x, flush=True)
    summary.append(str(x))


d = pd.read_pickle(os.path.join(OUT, "tx_cohort.pkl"))
d["cci_cat"] = pd.cut(d["cci"], [-1, 0, 1, 2, 4, 99], labels=["0", "1", "2", "3-4", "5+"]).astype(str)
d["medicaid"] = (d["ins_cat"] == "Medicaid").astype(float)
d["facility"] = d["dc_group"].isin(C.FACILITY_DESTS)
for k in C.DESTINATIONS:
    d[f"y_{k}"] = (d["dc_group"] == k).astype(float) * 100
d["y_facility"] = d["facility"].astype(float) * 100
d["y_snf_medicare"] = (d["status"].isin({"03", "83"})).astype(float) * 100
d["y_nf_medicaid_only"] = d["dc_medicaid_nf"].astype(float) * 100
d["y_swing"] = d["status"].isin({"61", "89"}).astype(float) * 100
d["y_los"] = d["los"].astype(float)
d["y_loglos"] = np.log(d["los"].clip(lower=1).astype(float))

BASE_COVARS = ["age_group", "sex", "race_cat", "eth_cat", "adm_type_cat", "adm_src_cat",
               "ed_flag", "apr_sev", "apr_rom", "apr_mdc", "cci_cat", "vent_any", "vent96",
               "discharge_q"]
DEST_OUT = ["y_home", "y_home_health", "y_snf", "y_irf", "y_ltch", "y_other", "y_facility"]
LABEL = {"y_home": "Home (self-care)", "y_home_health": "Home with home health",
         "y_snf": "SNF / nursing facility", "y_irf": "Inpatient rehabilitation (IRF)",
         "y_ltch": "Long-term acute care hospital", "y_other": "Other",
         "y_facility": "Any post-acute facility (SNF+IRF+LTCH)",
         "y_snf_medicare": "  SNF, Medicare-certified (03/83)",
         "y_nf_medicaid_only": "  Nursing facility, Medicaid-only (64/92)",
         "y_swing": "  Swing bed (61/89)",
         "y_los": "Length of stay, days",
         "y_loglos": "Length of stay, ratio (observed columns = geometric means)"}


# ---------------------------------------------------------------- samples
def base_sample(df, years=(Y0, Y1), icu="icu_rev", ages=C.AGE_18_64, groups=("Medicaid", "Private")):
    m = (df["year"].between(*years) & df[icu] & df["age_code"].isin(ages)
         & df["ins_cat"].isin(groups) & df["dc_group"].isin(C.DESTINATIONS)
         & df["los"].notna() & (df["los"] >= 1)
         & ~df["masked_hosp"] & ~df["non_acute_hosp"])
    return df[m].copy()


def flow(df):
    rows = []
    allc = pd.read_csv(os.path.join(OUT, "tx_all_discharges.csv"), index_col=0)["all_discharges"]
    rows.append(("All inpatient discharges", int(allc.loc[Y0:Y1].sum())))
    s = df[df["year"].between(Y0, Y1)]
    s = s[s["icu_rev"]]
    rows.append(("Adults with ICU/CCU accommodation charges", len(s)))
    s = s[s["age_code"].isin(C.AGE_18_64)]
    rows.append(("Aged 18-64", len(s)))
    for g in ["Medicare", "Self-pay/charity/unknown", "Other", "Missing/invalid"]:
        rows.append((f"  excluded: primary payer {g}", int((s["ins_cat"] == g).sum())))
    s = s[s["ins_cat"].isin(["Medicaid", "Private"])]
    rows.append(("Primary payer Medicaid or private", len(s)))
    for g in ["died", "hospice", "still_patient", "invalid"]:
        rows.append((f"  excluded: discharge status {g}", int((s["dc_group"] == g).sum())))
    s = s[s["dc_group"].isin(C.DESTINATIONS)]
    rows.append(("Discharged alive, not to hospice", len(s)))
    rows.append(("  excluded: masked/low-volume hospital ID", int(s["masked_hosp"].sum())))
    rows.append(("  excluded: specialty hospital without acute-care indicator",
                 int((~s["masked_hosp"] & s["non_acute_hosp"]).sum())))
    s = s[~s["masked_hosp"] & ~s["non_acute_hosp"]]
    rows.append(("  excluded: missing length of stay", int((s["los"].isna() | (s["los"] < 1)).sum())))
    s = s[s["los"].notna() & (s["los"] >= 1)]
    rows.append(("Analytic sample", len(s)))
    rows.append(("  Medicaid", int((s["ins_cat"] == "Medicaid").sum())))
    rows.append(("  Private", int((s["ins_cat"] == "Private").sum())))
    rows.append(("  Hospitals", s["hosp"].nunique()))
    return pd.DataFrame(rows, columns=["step", "n"])


# ---------------------------------------------------------------- estimation
def design(df, covars):
    X = pd.get_dummies(df[covars].astype(str), drop_first=True, dtype=float)
    X.insert(0, "medicaid", df["medicaid"].values)
    return X


def drop_collinear(X, tol=1e-7):
    """Drop exactly collinear dummy columns (e.g. APR-DRG severity 0, mortality-risk 0 and an
    ungroupable MDC all mark the same records). Uses a pivoted QR; 'medicaid' is never dropped."""
    from scipy.linalg import qr
    cols = ["medicaid"] + [c for c in X.columns if c != "medicaid"]
    A = X[cols].to_numpy(dtype=float)
    A = A / np.maximum(np.sqrt((A ** 2).sum(0)), 1e-300)
    _, R, piv = qr(A, mode="economic", pivoting=True)
    d = np.abs(np.diag(R))
    rank = int((d > tol * d.max()).sum())
    keep = sorted(piv[:rank])
    kept = [cols[i] for i in keep]
    dropped = [c for c in cols if c not in kept]
    if dropped and "medicaid" not in dropped:
        DROPPED.update(dropped)
    if "medicaid" in dropped:          # should not happen; fall back to keeping it first
        kept = ["medicaid"] + [c for c in kept if c != "medicaid"]
    return X[kept]


DROPPED = set()


def fe_ols(df, ys, covars, fe="hosp"):
    """Within-hospital OLS with hospital-clustered SEs for each outcome in `ys`
    (one shared design matrix). Returns {y: dict for the Medicaid term}."""
    df = df[df.groupby(fe)[fe].transform("size") > 1]
    X = design(df, covars)
    g = df[fe].values
    Xd = X - X.groupby(g).transform("mean")
    Xd = Xd[Xd.columns[(Xd.abs().sum() > 1e-9)]]
    Xd = drop_collinear(Xd)
    codes = pd.factorize(g)[0]
    out = {}
    for y in ys:
        if "medicaid" not in Xd.columns:
            out[y] = dict(b=np.nan, se=np.nan, lo=np.nan, hi=np.nan, p=np.nan, n=len(df), G=len(set(g)))
            continue
        yd = df[y] - df[y].groupby(g).transform("mean")
        r = sm.OLS(yd.values, Xd.values).fit(cov_type="cluster", cov_kwds={"groups": codes})
        i = list(Xd.columns).index("medicaid")
        b, se = r.params[i], r.bse[i]
        out[y] = dict(b=b, se=se, lo=b - 1.96 * se, hi=b + 1.96 * se, p=r.pvalues[i],
                      n=len(df), G=len(set(g)))
    return out


def results_block(df, covars, outcomes, label):
    rows = []
    priv = df[df["ins_cat"] != "Medicaid"]       # reference group: private (or Medicare for 65+)
    mcd = df[df["ins_cat"] == "Medicaid"]
    est = fe_ols(df, outcomes, covars)
    for y in outcomes:
        e = est[y]
        p0 = priv[y].mean()
        row = dict(model=label, outcome=LABEL[y], reference_obs=p0, medicaid_obs=mcd[y].mean(),
                   adj_diff=e["b"], lo=e["lo"], hi=e["hi"], p=e["p"], n=e["n"], hospitals=e["G"])
        if y == "y_loglos":
            row.update(reference_obs=np.exp(priv[y].mean()), medicaid_obs=np.exp(mcd[y].mean()),
                       adj_diff=np.exp(e["b"]), lo=np.exp(e["lo"]), hi=np.exp(e["hi"]))
        rows.append(row)
    return pd.DataFrame(rows)


def evalue(rr):
    if not np.isfinite(rr):
        return np.nan
    rr = rr if rr >= 1 else 1 / rr
    return rr + np.sqrt(rr * (rr - 1))


def suppress(df, cols):
    df = df.copy()
    for c in cols:
        df[c] = df[c].astype(object)
        df.loc[pd.to_numeric(df[c], errors="coerce") < C.SUPPRESS_BELOW, c] = "<11"
    return df


# ================================================================ run
fl = flow(d)
say("FLOW (primary years %d-%d)" % (Y0, Y1))
say(suppress(fl, ["n"]).to_string(index=False))

P = base_sample(d)
vol = P.groupby("hosp").size()
P["hosp_vol_tertile"] = P["hosp"].map(pd.qcut(vol.rank(method="first"), 3, labels=["low", "mid", "high"])).astype(str)
say(f"\nPrimary sample: {len(P):,} stays, {P['hosp'].nunique()} hospitals; "
    f"Medicaid {int(P['medicaid'].sum()):,}, private {int((1 - P['medicaid']).sum()):,}")
say(f"Hospitals with both Medicaid and private ICU stays: "
    f"{(P.groupby('hosp')['medicaid'].agg(['min', 'max']).pipe(lambda x: (x['min'] == 0) & (x['max'] == 1))).sum()}")

# ---- Table 1
t1 = []
for var in ["age_group", "sex", "race_cat", "eth_cat", "adm_type_cat", "adm_src_cat", "ed_flag",
            "apr_sev", "apr_rom", "cci_cat", "vent_any", "vent96", "teaching", "hosp_vol_tertile",
            "housing_econ_icd", "sud_icd", "smi_icd", "age_masked", "year"]:
    tab = pd.crosstab(P[var].astype(str), P["ins_cat"])
    pct = tab / tab.sum() * 100
    for lvl in tab.index:
        t1.append(dict(variable=var, level=lvl, medicaid_n=tab.loc[lvl].get("Medicaid", 0),
                       medicaid_pct=pct.loc[lvl].get("Medicaid", np.nan),
                       private_n=tab.loc[lvl].get("Private", 0),
                       private_pct=pct.loc[lvl].get("Private", np.nan)))
for var in ["los", "cci"]:
    for g in ["Medicaid", "Private"]:
        s = P.loc[P["ins_cat"] == g, var]
        t1.append(dict(variable=f"{var} mean (SD); median [IQR]", level=g,
                       medicaid_n=None, medicaid_pct=None, private_n=None, private_pct=None,
                       value=f"{s.mean():.2f} ({s.std():.2f}); {s.median():.0f} "
                             f"[{s.quantile(.25):.0f}-{s.quantile(.75):.0f}]"))
T1 = suppress(pd.DataFrame(t1), ["medicaid_n", "private_n"])

# ---- unadjusted destinations
dest_tab = pd.crosstab(P["dc_group"], P["ins_cat"])
dest_pct = (dest_tab / dest_tab.sum() * 100).round(2).add_suffix(" %")
status_tab = pd.crosstab(P["status"], P["ins_cat"])
DEST = suppress(pd.concat([dest_tab, dest_pct], axis=1).reset_index(), ["Medicaid", "Private"])
STATUS = suppress(status_tab.reset_index(), ["Medicaid", "Private"])

# ---- primary results
ALL_OUT = DEST_OUT + ["y_snf_medicare", "y_nf_medicaid_only", "y_swing", "y_los", "y_loglos"]
MAIN = results_block(P, BASE_COVARS, ALL_OUT, "Primary: hospital + quarter FE")
if DROPPED:
    say(f"(collinear dummy columns dropped: {sorted(DROPPED)})")
say("\nPRIMARY RESULTS (percentage points for destinations; days or ratio for LOS)")
say(MAIN[["outcome", "reference_obs", "medicaid_obs", "adj_diff", "lo", "hi"]].round(2).to_string(index=False))

# unadjusted and between-hospital comparison (how much of the gap is across hospitals)
UNADJ = results_block(P.assign(one="1"), ["discharge_q"], ["y_snf", "y_facility", "y_los"],
                      "Quarter FE + hospital FE only")
NOFE = []
for y in ["y_snf", "y_facility", "y_los"]:
    X = drop_collinear(sm.add_constant(design(P, BASE_COVARS + ["teaching", "hosp_vol_tertile"])))
    r = sm.OLS(P[y].values, X.values).fit(cov_type="cluster",
                                          cov_kwds={"groups": pd.factorize(P["hosp"])[0]})
    i = list(X.columns).index("medicaid")
    NOFE.append(dict(model="Covariates, no hospital FE", outcome=LABEL[y], adj_diff=r.params[i],
                     lo=r.params[i] - 1.96 * r.bse[i], hi=r.params[i] + 1.96 * r.bse[i]))
DECOMP = pd.concat([UNADJ, pd.DataFrame(NOFE), MAIN[MAIN["outcome"].isin(
    [LABEL[y] for y in ["y_snf", "y_facility", "y_los"]])]], ignore_index=True)

# ---- E-values (risk ratio implied by the adjusted difference). For rare outcomes the linear
# model can imply a negative adjusted risk; those rows are left blank and the multinomial-logit
# risk ratios (sheet evalues, source = mnlogit) are the ones to report.
ev = []
for y in ["y_snf", "y_facility", "y_irf", "y_ltch", "y_home_health"]:
    r = MAIN[MAIN["outcome"] == LABEL[y]].iloc[0]
    p0 = r.reference_obs
    p1, b_lo, b_hi = p0 + r.adj_diff, p0 + r.lo, p0 + r.hi
    if min(p1, b_lo, b_hi) <= 0:
        ev.append(dict(source="LPM", outcome=r.outcome, rr=np.nan, e_value=np.nan, e_value_ci=np.nan,
                       note="adjusted risk <= 0 under the linear model; use mnlogit row"))
        continue
    rr = p1 / p0
    rr_ci = b_lo / p0 if rr >= 1 else b_hi / p0
    ev.append(dict(source="LPM", outcome=r.outcome, rr=rr, e_value=evalue(rr),
                   e_value_ci=1.0 if (rr_ci - 1) * (rr - 1) <= 0 else evalue(rr_ci), note=""))
EV = pd.DataFrame(ev)

# ---- sensitivity analyses
SENS_OUT = ["y_snf", "y_irf", "y_facility", "y_home", "y_los"]
sens = []


def add(label, df, covars=BASE_COVARS):
    if len(df) < 200 or df["medicaid"].nunique() < 2:
        say(f"  skipped {label}: too few records")
        return
    sens.append(results_block(df, covars, SENS_OUT, label))


add("Primary", P)
add("S1 excl. AMA and acute-hospital transfers", P[~P["dc_ama"] & ~P["dc_acute_transfer"]])
add("S2 ICU/CCU was the unit with the most days", base_sample(d, icu="icu_spec1")
    if "icu_spec1" in d else P.iloc[:0])
add("S3 invasive mechanical ventilation cohort", P[P["vent_any"]])
add("S4 + ICD social-needs proxies", P, BASE_COVARS + ["housing_econ_icd", "sud_icd", "smi_icd"])
add("S5a 2020 only (COVID year)", base_sample(d, years=(2020, 2020)))
add("S5b 2017-2020 pooled", base_sample(d, years=(Y0, 2020)))
add("S6 without APR-DRG severity, mortality risk, MDC",
    P, [c for c in BASE_COVARS if c not in ("apr_sev", "apr_rom", "apr_mdc")])
add("S7 excl. private with Medicaid secondary payer", P[~((P["ins_cat"] == "Private") & P["sec_medicaid"])])
add("S8 excl. transfers in from another facility", P[~P["adm_src_cat"].str.startswith("Transfer")])
add("S9 excl. age/sex-masked records (HIV, SUD)", P[~P["age_masked"]])
add("S10 LOS trimmed at 99th percentile", P[P["los"] <= P["los"].quantile(.99)])
add("S11 excl. pregnancy/childbirth stays (APR MDC 14)", P[P["apr_mdc"].astype(str) != "14"])
add("H1 teaching hospitals", P[P["teaching"].eq(True)])
add("H2 non-teaching hospitals", P[P["teaching"].eq(False)])
add("H3 not mechanically ventilated", P[~P["vent_any"]])
SENS = pd.concat(sens, ignore_index=True)
wide = SENS.pivot_table(index="model", columns="outcome", values="adj_diff", sort=False).round(2)
say("\nSENSITIVITY (adjusted Medicaid - private difference)")
say(wide.to_string())

# ---- LOS by destination (stratified, descriptive of where the extra days sit)
bydest = []
for lab, m in [("Discharged home (self-care or home health)", P["dc_group"].isin(["home", "home_health"])),
               ("Discharged to post-acute facility", P["facility"])]:
    bydest.append(results_block(P[m], BASE_COVARS, ["y_los", "y_loglos"], lab))
BYDEST = pd.concat(bydest, ignore_index=True)

# ---- 65+ : Medicaid vs Medicare as primary payer (dual status is not observable)
Q = base_sample(d, ages=C.AGE_65_PLUS, groups=("Medicaid", "Medicare"))
OLD = results_block(Q, BASE_COVARS, ["y_snf", "y_irf", "y_ltch", "y_facility", "y_home", "y_los"],
                    "65+: Medicaid vs Medicare primary payer") if Q["medicaid"].nunique() == 2 and (Q["medicaid"].sum() >= 11) else pd.DataFrame()

# ---- multinomial logit with g-computation (MIMIC-style), no hospital FE
MN = pd.DataFrame()
try:
    covs = BASE_COVARS + ["teaching", "hosp_vol_tertile"]
    X = sm.add_constant(design(P, covs))
    X = drop_collinear(X.loc[:, (X.std() > 0) | (X.columns == "const")])
    ycat = pd.Categorical(P["dc_group"], categories=C.DESTINATIONS).codes

    def aap(Xm, yv, start=None):
        sp = None if start is None else np.ravel(start, order="F")
        mod = sm.MNLogit(yv, Xm.values)
        r = mod.fit(method="newton", maxiter=60, disp=False, start_params=sp)
        if not (np.all(np.isfinite(r.params)) and r.mle_retvals.get("converged", True)):
            # Newton can overshoot with sparse categories; fall back to L-BFGS
            r = mod.fit(method="lbfgs", maxiter=2000, disp=False, start_params=sp)
        if not np.all(np.isfinite(r.params)):
            raise RuntimeError("not converged")
        X1, X0 = Xm.copy(), Xm.copy()
        X1["medicaid"], X0["medicaid"] = 1.0, 0.0
        p1, p0 = r.predict(X1.values).mean(0), r.predict(X0.values).mean(0)
        return (p1 - p0) * 100, p0 * 100, p1 * 100, r.params

    diff, p0, p1, par = aap(X, ycat)
    boots, boot_rr = [], []
    if args.boot:
        hosp = P["hosp"].values
        H = np.unique(hosp)
        idx_by_h = {h: np.where(hosp == h)[0] for h in H}
        for b in range(args.boot):
            hs = rng.choice(H, len(H), replace=True)
            ix = np.concatenate([idx_by_h[h] for h in hs])
            try:
                bd, bp0, bp1, _ = aap(X.iloc[ix].reset_index(drop=True), ycat[ix], start=par)
                boots.append(bd)
                boot_rr.append(bp1 / bp0)
            except Exception as ex:
                if b == 0:
                    print("  bootstrap rep failed:", repr(ex)[:200])
            if (b + 1) % 20 == 0:
                print(f"  bootstrap {b + 1}/{args.boot}", flush=True)
    boots = np.array(boots) if boots else None
    MN = pd.DataFrame(dict(destination=C.DESTINATIONS, private_adj=p0, medicaid_adj=p1, diff=diff,
                           lo=np.percentile(boots, 2.5, 0) if boots is not None else np.nan,
                           hi=np.percentile(boots, 97.5, 0) if boots is not None else np.nan))
    MN["reps_used"] = len(boots) if boots is not None else 0
    MN["reps_requested"] = args.boot
    MN["rr"] = p1 / p0
    if boot_rr:
        br = np.array(boot_rr)
        MN["rr_lo"], MN["rr_hi"] = np.percentile(br, 2.5, 0), np.percentile(br, 97.5, 0)
    else:
        MN["rr_lo"] = MN["rr_hi"] = np.nan
    for _, r in MN.iterrows():
        bound = r.rr_lo if r.rr >= 1 else r.rr_hi
        ev.append(dict(source="mnlogit", outcome=r.destination, rr=r.rr, e_value=evalue(r.rr),
                       e_value_ci=np.nan if not np.isfinite(bound) else
                       (1.0 if (bound - 1) * (r.rr - 1) <= 0 else evalue(bound)),
                       note="" if boot_rr else "run with --boot for the CI bound"))
    EV = pd.DataFrame(ev)
    say("\nMULTINOMIAL LOGIT, average adjusted predictions (no hospital FE)")
    say(MN.round(2).to_string(index=False))
except Exception as ex:
    say(f"\nMultinomial logit check failed: {ex}")

say("\nE-VALUES")
say(EV.round(2).to_string(index=False))

# ---- hospital-level descriptives (aggregate)
hstat = P.groupby("hosp").agg(n=("rid", "size"), pct_medicaid=("medicaid", "mean"))
HOSP = pd.DataFrame([dict(hospitals=len(hstat), median_icu_stays=hstat["n"].median(),
                          p25=hstat["n"].quantile(.25), p75=hstat["n"].quantile(.75),
                          median_pct_medicaid=100 * hstat["pct_medicaid"].median(),
                          hospitals_ge_11_each_group=int(((P.groupby("hosp")["medicaid"].sum() >= 11)
                                                          & (P.groupby("hosp")["medicaid"].apply(lambda s: (1 - s).sum()) >= 11)).sum()))])

# ---- write
path = os.path.join(OUT, "tx_results.xlsx")
with pd.ExcelWriter(path) as xw:
    suppress(fl, ["n"]).to_excel(xw, sheet_name="flow", index=False)
    T1.to_excel(xw, sheet_name="table1", index=False)
    DEST.to_excel(xw, sheet_name="destinations_unadj", index=False)
    STATUS.to_excel(xw, sheet_name="pat_status_codes", index=False)
    MAIN.to_excel(xw, sheet_name="primary", index=False)
    DECOMP.to_excel(xw, sheet_name="fe_vs_nofe", index=False)
    EV.to_excel(xw, sheet_name="evalues", index=False)
    SENS.to_excel(xw, sheet_name="sensitivity_long", index=False)
    wide.to_excel(xw, sheet_name="sensitivity_wide")
    BYDEST.to_excel(xw, sheet_name="los_by_destination", index=False)
    OLD.to_excel(xw, sheet_name="age65plus", index=False)
    MN.to_excel(xw, sheet_name="mnlogit_gcomp", index=False)
    HOSP.to_excel(xw, sheet_name="hospitals", index=False)
with open(os.path.join(OUT, "tx_results_summary.txt"), "w") as fh:
    fh.write("\n".join(summary))
print(f"\nWrote {path} and tx_results_summary.txt (aggregate only; safe to share).")
