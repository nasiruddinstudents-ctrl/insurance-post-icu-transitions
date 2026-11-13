"""
03_revision.py - additional analyses requested in peer review (Texas PUDF).

Run after 01_build_cohort.py (rebuild once with the current version so ICU and CCU charges
are stored separately):
    python 03_revision.py

Writes tx_output/tx_revision.xlsx and tx_output/tx_revision_summary.txt (aggregate only).

Contents
  ladder       sequential adjustment: M0 admission-time characteristics -> M1 + Charlson ->
               M2 + ventilation -> M3 + APR-DRG severity, mortality risk, MDC (= paper model)
               -> M4 + diagnosis-coded social-needs proxies
  identify     identifying sample: stays in hospitals with both payers, overlap of the
               hospital Medicaid share, overlap-restricted re-estimate
  influence    leave-one-hospital-out range; excluding the 10 largest hospitals;
               hospital-level (aggregated) estimates
  lpm_fit      distribution of fitted probabilities from the linear probability models
  icu_mix      ICU-only / CCU-only / both charge composition by payer and estimates by type
  missing      missing or masked values by payer
  mnl_delta    multinomial logit with hospital-cluster-robust (delta-method) confidence
               intervals for adjusted probabilities, differences, and risk ratios
"""
import os
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.linalg import qr

import tx_common as C

warnings.filterwarnings("ignore")
OUT = "tx_output"
log = []


def say(x=""):
    print(x, flush=True)
    log.append(str(x))


d = pd.read_pickle(os.path.join(OUT, "tx_cohort.pkl"))
d["cci_cat"] = pd.cut(d["cci"], [-1, 0, 1, 2, 4, 99], labels=["0", "1", "2", "3-4", "5+"]).astype(str)
d["medicaid"] = (d["ins_cat"] == "Medicaid").astype(float)
for k in C.DESTINATIONS:
    d[f"y_{k}"] = (d["dc_group"] == k).astype(float) * 100
d["y_facility"] = d["dc_group"].isin(C.FACILITY_DESTS).astype(float) * 100
d["y_los"] = d["los"].astype(float)
OUTS = ["y_home", "y_home_health", "y_snf", "y_irf", "y_ltch", "y_other", "y_facility", "y_los"]
LAB = dict(zip(OUTS, ["Home", "Home health", "SNF / nursing facility", "IRF", "LTCH", "Other", "Any facility", "LOS, days"]))

m = (d["year"].between(2017, 2019) & d["icu_rev"] & d["age_code"].isin(C.AGE_18_64)
     & d["ins_cat"].isin(["Medicaid", "Private"]) & d["dc_group"].isin(C.DESTINATIONS)
     & d["los"].notna() & (d["los"] >= 1) & ~d["masked_hosp"] & ~d["non_acute_hosp"])
P = d[m].copy()
P = P[P.groupby("hosp")["hosp"].transform("size") > 1].copy()
say(f"Primary sample (hospitals with >1 stay): {len(P):,} stays, {P['hosp'].nunique()} hospitals")

M0 = ["age_group", "sex", "race_cat", "eth_cat", "adm_type_cat", "adm_src_cat", "ed_flag", "discharge_q"]
M1 = M0 + ["cci_cat"]
M2 = M1 + ["vent_any", "vent96"]
M3 = M2 + ["apr_sev", "apr_rom", "apr_mdc"]
M4 = M3 + ["housing_econ_icd", "sud_icd", "smi_icd"]


def design(df, covars):
    X = pd.get_dummies(df[covars].astype(str), drop_first=True, dtype=float)
    X.insert(0, "medicaid", df["medicaid"].values)
    return X


def drop_collinear(X, tol=1e-7):
    cols = ["medicaid"] + [c for c in X.columns if c != "medicaid"]
    A = X[cols].to_numpy(dtype=float)
    A = A / np.maximum(np.sqrt((A ** 2).sum(0)), 1e-300)
    _, R, piv = qr(A, mode="economic", pivoting=True)
    dd = np.abs(np.diag(R))
    keep = sorted(piv[:int((dd > tol * dd.max()).sum())])
    kept = [cols[i] for i in keep]
    if "medicaid" not in kept:
        kept = ["medicaid"] + kept
    return X[kept]


class FE:
    """Within-hospital design reused across outcomes (and for leave-one-out)."""

    def __init__(self, df, covars):
        self.df = df
        X = design(df, covars)
        self.g = df["hosp"].values
        Xd = X - X.groupby(self.g).transform("mean")
        Xd = Xd[Xd.columns[(Xd.abs().sum() > 1e-9)]]
        self.Xd = drop_collinear(Xd)
        self.codes = pd.factorize(self.g)[0]
        self.i = list(self.Xd.columns).index("medicaid")

    def ydm(self, y):
        return (self.df[y] - self.df[y].groupby(self.g).transform("mean")).values

    def fit(self, y):
        r = sm.OLS(self.ydm(y), self.Xd.values).fit(cov_type="cluster", cov_kwds={"groups": self.codes})
        b, se = r.params[self.i], r.bse[self.i]
        return dict(b=b, se=se, lo=b - 1.96 * se, hi=b + 1.96 * se, params=r.params)


# ---------------------------------------------------------------- 1. adjustment ladder
rows = []
fe_store = {}
for name, cov in [("M0 admission-time characteristics", M0), ("M1 + Charlson index", M1),
                  ("M2 + invasive ventilation", M2), ("M3 + APR-DRG severity, risk, MDC (paper model)", M3),
                  ("M4 + diagnosis-coded social-needs proxies", M4)]:
    fe = FE(P, cov)
    fe_store[name[:2]] = fe
    for y in OUTS:
        e = fe.fit(y)
        rows.append(dict(model=name, outcome=LAB[y], diff=e["b"], lo=e["lo"], hi=e["hi"], se=e["se"]))
    say(f"  ladder {name}: SNF {rows[-6]['diff']:.2f}, IRF {rows[-5]['diff']:.2f}, LOS {rows[-1]['diff']:.2f}")
LADDER = pd.DataFrame(rows)

# ---------------------------------------------------------------- 2. identifying sample / overlap
hs = P.groupby("hosp").agg(n=("rid", "size"), nM=("medicaid", "sum"))
hs["nP"] = hs["n"] - hs["nM"]
hs["shareM"] = hs["nM"] / hs["n"]
both = hs[(hs["nM"] > 0) & (hs["nP"] > 0)]
ge11 = hs[(hs["nM"] >= 11) & (hs["nP"] >= 11)]
ov = hs[(hs["nM"] >= 11) & (hs["nP"] >= 11) & hs["shareM"].between(0.05, 0.95)]
IDENT = pd.DataFrame([
    dict(item="Hospitals in fixed-effects sample", value=len(hs)),
    dict(item="Hospitals with both Medicaid and private stays", value=len(both)),
    dict(item="Stays in hospitals with both payers, %", value=100 * both["n"].sum() / hs["n"].sum()),
    dict(item="Hospitals with >=11 stays of each payer", value=len(ge11)),
    dict(item="Stays in those hospitals, %", value=100 * ge11["n"].sum() / hs["n"].sum()),
    dict(item="Hospital Medicaid share p10", value=100 * hs["shareM"].quantile(.10)),
    dict(item="Hospital Medicaid share p25", value=100 * hs["shareM"].quantile(.25)),
    dict(item="Hospital Medicaid share median", value=100 * hs["shareM"].median()),
    dict(item="Hospital Medicaid share p75", value=100 * hs["shareM"].quantile(.75)),
    dict(item="Hospital Medicaid share p90", value=100 * hs["shareM"].quantile(.90)),
    dict(item="Hospitals in overlap sample (>=11 each, share 5-95%)", value=len(ov)),
    dict(item="Stays in overlap sample", value=int(ov["n"].sum())),
])
say("\nIDENTIFYING SAMPLE"); say(IDENT.round(1).to_string(index=False))
Pov = P[P["hosp"].isin(ov.index)]
fe_ov = FE(Pov, M3)
OVER = pd.DataFrame([dict(model="Overlap-restricted (M3)", outcome=LAB[y], **{k: v for k, v in fe_ov.fit(y).items() if k != "params"}) for y in OUTS])

# ---------------------------------------------------------------- 3. influence
fe3 = fe_store["M3"]
X = fe3.Xd.values
XtX = X.T @ X
loo = []
for y in ["y_snf", "y_irf", "y_ltch", "y_facility", "y_los"]:
    yd = fe3.ydm(y)
    Xty = X.T @ yd
    full = np.linalg.solve(XtX, Xty)[fe3.i]
    est = []
    for h in np.unique(fe3.g):
        ix = fe3.g == h
        Xh, yh = X[ix], yd[ix]
        try:
            est.append(np.linalg.solve(XtX - Xh.T @ Xh, Xty - Xh.T @ yh)[fe3.i])
        except np.linalg.LinAlgError:
            pass
    est = np.array(est)
    loo.append(dict(outcome=LAB[y], full=full, loo_min=est.min(), loo_max=est.max(), loo_sd=est.std()))
LOO = pd.DataFrame(loo)
say("\nLEAVE-ONE-HOSPITAL-OUT"); say(LOO.round(2).to_string(index=False))
big = hs["n"].sort_values(ascending=False).index[:10]
fe_nb = FE(P[~P["hosp"].isin(big)], M3)
NOBIG = pd.DataFrame([dict(model="Excluding 10 largest hospitals (M3)", outcome=LAB[y], **{k: v for k, v in fe_nb.fit(y).items() if k != "params"}) for y in OUTS])
# hospital-level aggregation: crude within-hospital difference, precision-weighted and median
agg = []
for y in ["y_snf", "y_irf", "y_ltch", "y_facility", "y_los"]:
    g = P[P["hosp"].isin(ge11.index)].groupby(["hosp", "medicaid"])[y].agg(["mean", "var", "size"]).unstack()
    diff = g["mean"][1.0] - g["mean"][0.0]
    var = g["var"][1.0] / g["size"][1.0] + g["var"][0.0] / g["size"][0.0]
    w = 1 / var.replace(0, np.nan)
    agg.append(dict(outcome=LAB[y], hospitals=int(diff.notna().sum()), weighted_mean=float((diff * w).sum() / w.sum()),
                    median=float(diff.median()), share_positive=float((diff > 0).mean() * 100)))
AGG = pd.DataFrame(agg)
say("\nHOSPITAL-LEVEL (crude within-hospital differences)"); say(AGG.round(2).to_string(index=False))

# ---------------------------------------------------------------- 4. LPM fitted values
fit_rows = []
for y in ["y_snf", "y_irf", "y_ltch", "y_home_health", "y_other"]:
    e = fe3.fit(y)
    yhat = P[y].groupby(fe3.g).transform("mean").values + X @ e["params"]
    fit_rows.append(dict(outcome=LAB[y], pct_below_0=100 * (yhat < 0).mean(), pct_above_100=100 * (yhat > 100).mean(),
                         p1=np.percentile(yhat, 1), p50=np.percentile(yhat, 50), p99=np.percentile(yhat, 99)))
LPMFIT = pd.DataFrame(fit_rows)
say("\nLPM FITTED VALUES (percent scale)"); say(LPMFIT.round(2).to_string(index=False))

# ---------------------------------------------------------------- 5. ICU / CCU composition
if "icu_chg" in P:
    P["icu_type"] = np.select([P["icu_chg"] & ~P["ccu_chg"], ~P["icu_chg"] & P["ccu_chg"]], ["ICU only", "CCU only"], "ICU and CCU")
    mix = pd.crosstab(P["icu_type"], P["ins_cat"], normalize="columns") * 100
    MIX = mix.reset_index()
    mixest = []
    for t in ["ICU only", "CCU only"]:
        sub = P[P["icu_type"] == t]
        sub = sub[sub.groupby("hosp")["hosp"].transform("size") > 1]
        if sub["medicaid"].nunique() == 2 and len(sub) > 1000:
            f = FE(sub, M3)
            for y in ["y_snf", "y_irf", "y_ltch", "y_facility", "y_los"]:
                e = f.fit(y)
                mixest.append(dict(model=t, n=len(sub), outcome=LAB[y], diff=e["b"], lo=e["lo"], hi=e["hi"]))
    MIXEST = pd.DataFrame(mixest)
    say("\nICU/CCU MIX (% of stays)"); say(MIX.round(1).to_string(index=False))
else:
    MIX = MIXEST = pd.DataFrame([dict(note="Rebuild with the current 01_build_cohort.py to separate ICU and CCU charges")])

# ---------------------------------------------------------------- 6. missing / masked values
miss = []
for lab, cond in [("Race unknown", P["race_cat"].astype(str).eq("Unknown")), ("Ethnicity unknown", P["eth_cat"].astype(str).eq("Unknown")),
                  ("Sex masked or unknown", P["sex"].astype(str).eq("U")), ("Age group masked", P["age_masked"].astype(bool)),
                  ("Admission type other/unknown", P["adm_type_cat"].astype(str).eq("Other/unknown")),
                  ("Admission source other/unknown", P["adm_src_cat"].astype(str).eq("Other/unknown")),
                  ("APR-DRG ungroupable (severity 0)", P["apr_sev"].astype(str).eq("0"))]:
    miss.append(dict(item=lab, medicaid_pct=100 * cond[P["medicaid"] == 1].mean(), private_pct=100 * cond[P["medicaid"] == 0].mean()))
MISS = pd.DataFrame(miss)
say("\nMISSING / MASKED (%)"); say(MISS.round(2).to_string(index=False))

# ---------------------------------------------------------------- 7. multinomial logit, cluster-robust delta method
MND = pd.DataFrame()
try:
    vol = P.groupby("hosp").size()
    P["vol_t"] = P["hosp"].map(pd.qcut(vol.rank(method="first"), 3, labels=["low", "mid", "high"])).astype(str)
    Xm = sm.add_constant(design(P, M3 + ["teaching", "vol_t"]))
    Xm = drop_collinear(Xm.loc[:, (Xm.std() > 0) | (Xm.columns == "const")])
    yc = pd.Categorical(P["dc_group"], categories=C.DESTINATIONS).codes
    codes = pd.factorize(P["hosp"])[0]
    mod = sm.MNLogit(yc, Xm.values)
    r = mod.fit(method="newton", maxiter=60, disp=False)
    if not np.all(np.isfinite(r.params)):
        r = mod.fit(method="lbfgs", maxiter=3000, disp=False)
    B = np.asarray(r.params)                         # k x (J-1)
    V = np.asarray(mod.fit(start_params=B.ravel(order="F"), method="newton", maxiter=0, disp=False,
                           cov_type="cluster", cov_kwds={"groups": codes}).cov_params())
    im = list(Xm.columns).index("medicaid")
    k, J1 = B.shape

    def probs(Xv):
        e = np.exp(np.clip(Xv @ B, -50, 50))
        den = 1 + e.sum(1, keepdims=True)
        return np.column_stack([1 / den[:, 0], e / den])

    X1, X0 = Xm.values.copy(), Xm.values.copy()
    X1[:, im], X0[:, im] = 1.0, 0.0
    P1, P0 = probs(X1), probs(X0)
    A1, A0 = P1.mean(0), P0.mean(0)

    def grad(Pm, Xv):          # d mean(P_j) / d B (k x J-1), flattened equation-major
        G = np.zeros((Pm.shape[1], k * J1))
        for j in range(Pm.shape[1]):
            for mm in range(J1):
                w = Pm[:, j] * ((j == mm + 1) - Pm[:, mm + 1])
                G[j, mm * k:(mm + 1) * k] = (w @ Xv) / len(Xv)
        return G

    G1, G0 = grad(P1, X1), grad(P0, X0)
    Gd = G1 - G0
    se_d = np.sqrt(np.einsum("ij,jk,ik->i", Gd, V, Gd))
    Gl = G1 / A1[:, None] - G0 / A0[:, None]
    se_l = np.sqrt(np.einsum("ij,jk,ik->i", Gl, V, Gl))
    rr = A1 / A0
    MND = pd.DataFrame(dict(destination=C.DESTINATIONS, private_adj=100 * A0, medicaid_adj=100 * A1, diff=100 * (A1 - A0),
                            lo=100 * (A1 - A0 - 1.96 * se_d), hi=100 * (A1 - A0 + 1.96 * se_d),
                            rr=rr, rr_lo=rr * np.exp(-1.96 * se_l), rr_hi=rr * np.exp(1.96 * se_l)))
    say("\nMULTINOMIAL LOGIT, hospital-cluster-robust delta-method CIs"); say(MND.round(3).to_string(index=False))
except Exception as ex:
    say(f"multinomial delta-method step failed: {ex}")

with pd.ExcelWriter(os.path.join(OUT, "tx_revision.xlsx")) as xw:
    LADDER.to_excel(xw, sheet_name="ladder", index=False)
    IDENT.to_excel(xw, sheet_name="identify", index=False)
    OVER.to_excel(xw, sheet_name="overlap_model", index=False)
    LOO.to_excel(xw, sheet_name="leave_one_out", index=False)
    NOBIG.to_excel(xw, sheet_name="excl_10_largest", index=False)
    AGG.to_excel(xw, sheet_name="hospital_level", index=False)
    LPMFIT.to_excel(xw, sheet_name="lpm_fit", index=False)
    MIX.to_excel(xw, sheet_name="icu_mix", index=False)
    MIXEST.to_excel(xw, sheet_name="icu_mix_estimates", index=False)
    MISS.to_excel(xw, sheet_name="missing", index=False)
    MND.to_excel(xw, sheet_name="mnl_delta", index=False)
with open(os.path.join(OUT, "tx_revision_summary.txt"), "w") as fh:
    fh.write("\n".join(log))
print("\nWrote tx_output/tx_revision.xlsx and tx_revision_summary.txt (aggregate only).")
