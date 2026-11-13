"""
04_bounded.py - bounded-probability, within-hospital models requested in the second review.

Run after rebuilding the cohort with the current 01_build_cohort.py (it now stores the
present-on-admission Charlson index, cci_poa):
    python 04_bounded.py              # about 20-60 minutes
    python 04_bounded.py --boot 200   # adds a hospital-cluster bootstrap check (several hours)

Writes tx_output/tx_bounded.xlsx and tx_output/tx_bounded_summary.txt (aggregate only;
counts under 11 suppressed).

Models (one binary destination at a time, hospital fixed effects kept in every model)
  FE-logit    logit with a fixed effect for every hospital (unconditional ML, hospitals are
              large). Adjusted probabilities by marginal standardization over the analytic
              sample (Muller & MacLehose 2014): every stay predicted as Medicaid and as private,
              then averaged. Gives an adjusted probability difference (percentage points) and
              an adjusted marginal probability ratio. Delta-method CIs from the
              hospital-clustered sandwich (the same calculation as Stata `margins` after
              `logit ... i.hosp, vce(cluster hosp)`). Predicted probabilities always lie in 0-1.
  FE-Poisson  Poisson with hospital fixed effects (concentrated likelihood), robust
              hospital-clustered SEs. exp(b) is the conditional risk ratio used for E-values.
  LPM         linear probability model of 02/03 (for side-by-side comparison).

Sheets
  bounded        M0..M4 (+ POA-Charlson variants) x outcome x model
  evalues        E-values (point and CI limit) from the marginal ratio and the Poisson RR
  calibration    deciles of fitted probability: mean fitted vs observed, by payer (M3)
  fitted_dist    fitted-value distributions by payer for LPM / FE-logit / FE-Poisson (M3)
  fitted_hosp    distribution across hospitals of mean fitted probability by payer (M3)
  fit_stats      Brier score and share of fitted values outside 0-1 by model
  influence      approximate leave-one-hospital-out range of the FE-logit Medicaid log-odds,
                 and refit excluding the 10 largest hospitals
  subcohorts     ICU-only, CCU-only, ICU/CCU unit with most days, excluding psychiatric and
                 substance-use MDCs / psychiatric hospitals: counts by payer and estimates
  poa            how often POA was reported, and Charlson (all codes) vs Charlson (POA only)
  flow_by_payer  2017-2019 exclusions by payer
  boot           (only with --boot) cluster-bootstrap percentile CIs, seed 20261113
"""
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.linalg import qr
from scipy.special import expit

import tx_common as C

warnings.filterwarnings("ignore")
OUT = "tx_output"
SEED = 20261113
BOOT = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 0
log = []
T0 = time.time()


def say(x=""):
    print(x, flush=True)
    log.append(str(x))


def supp(n):
    return np.where(np.asarray(n) < C.SUPPRESS_BELOW, np.nan, n)


# ---------------------------------------------------------------- data (same sample as 03)
d = pd.read_pickle(os.path.join(OUT, "tx_cohort.pkl"))
if "cci_poa" not in d:
    sys.exit("tx_cohort.pkl has no cci_poa column: re-run 01_build_cohort.py with the current code first.")
cut = lambda s: pd.cut(s, [-1, 0, 1, 2, 4, 99], labels=["0", "1", "2", "3-4", "5+"]).astype(str)
d["cci_cat"], d["cci_poa_cat"] = cut(d["cci"]), cut(d["cci_poa"])
d["medicaid"] = (d["ins_cat"] == "Medicaid").astype(float)
for k in C.DESTINATIONS:
    d[f"y_{k}"] = (d["dc_group"] == k).astype(float)
d["y_facility"] = d["dc_group"].isin(C.FACILITY_DESTS).astype(float)
# sample flow by payer (2017-2019, ICU/CCU charges, aged 18-64, Medicaid or private)
s = d[d["year"].between(2017, 2019) & d["icu_rev"] & d["age_code"].isin(C.AGE_18_64) & d["ins_cat"].isin(["Medicaid", "Private"])]
fl = [("Primary payer Medicaid or private", np.ones(len(s), bool))]
for g_ in ["died", "hospice", "still_patient", "invalid"]:
    fl.append((f"excluded: discharge status {g_}", (s["dc_group"] == g_).values))
alive = s["dc_group"].isin(C.DESTINATIONS).values
fl.append(("excluded: masked hospital ID", alive & s["masked_hosp"].values))
fl.append(("excluded: specialty hospital without acute-care indicator", alive & ~s["masked_hosp"].values & s["non_acute_hosp"].values))
ok_ = alive & ~s["masked_hosp"].values & ~s["non_acute_hosp"].values
fl.append(("excluded: missing length of stay", ok_ & ~(s["los"].notna() & (s["los"] >= 1)).values))
fin = ok_ & (s["los"].notna() & (s["los"] >= 1)).values
fl.append(("Analytic sample", fin))
fl.append(("of which in hospitals with a single stay (no within-hospital contribution)", fin & s["hosp"].map(s[fin]["hosp"].value_counts()).fillna(0).eq(1).values))
FLOW = pd.DataFrame([dict(step=a, medicaid=int((b & (s["ins_cat"] == "Medicaid").values).sum()),
                          private=int((b & (s["ins_cat"] == "Private").values).sum())) for a, b in fl])
FLOW["medicaid"], FLOW["private"] = supp(FLOW["medicaid"]), supp(FLOW["private"])
del s

m = (d["year"].between(2017, 2019) & d["icu_rev"] & d["age_code"].isin(C.AGE_18_64)
     & d["ins_cat"].isin(["Medicaid", "Private"]) & d["dc_group"].isin(C.DESTINATIONS)
     & d["los"].notna() & (d["los"] >= 1) & ~d["masked_hosp"] & ~d["non_acute_hosp"])
P = d[m].copy()
del d
P = P[P.groupby("hosp")["hosp"].transform("size") > 1].reset_index(drop=True)
# rare APR MDC levels pooled so no level perfectly predicts a rare destination
mdc_n = P["apr_mdc"].astype(str).value_counts()
P["apr_mdc"] = P["apr_mdc"].astype(str).where(P["apr_mdc"].astype(str).map(mdc_n) >= 500, "pooled")
say(f"Primary sample: {len(P):,} stays, {P['hosp'].nunique()} hospitals")

OUTS = ["y_snf", "y_irf", "y_ltch", "y_home_health", "y_home", "y_other", "y_facility"]
LAB = dict(zip(OUTS, ["SNF / nursing facility", "IRF", "LTCH", "Home health", "Home", "Other", "Any facility"]))
P["y_los"] = P["los"].astype(float)
LAB["y_los"] = "LOS, days"
M0 = ["age_group", "sex", "race_cat", "eth_cat", "adm_type_cat", "adm_src_cat", "ed_flag", "discharge_q"]
MODELS = {
    "M0": M0,
    "M1": M0 + ["cci_cat"],
    "M1p": M0 + ["cci_poa_cat"],
    "M3": M0 + ["cci_cat", "vent_any", "vent96", "apr_sev", "apr_rom", "apr_mdc"],
    "M3p": M0 + ["cci_poa_cat", "vent_any", "vent96", "apr_sev", "apr_rom", "apr_mdc"],
    "M4": M0 + ["cci_cat", "vent_any", "vent96", "apr_sev", "apr_rom", "apr_mdc",
                "housing_econ_icd", "sud_icd", "smi_icd"],
}
MLAB = {"M0": "M0 admission-time characteristics", "M1": "M1 + Charlson (all codes)",
        "M1p": "M1p + Charlson (present-on-admission codes only)",
        "M3": "M3 + ventilation, APR-DRG severity, risk, MDC",
        "M3p": "M3p = M3 with POA-only Charlson", "M4": "M4 + social-needs proxies"}


# ---------------------------------------------------------------- design helpers
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
    return X[(["medicaid"] if "medicaid" not in kept else []) + kept]


class Design:
    """Hospital-FE design: columns that vary within hospital and are not collinear."""

    def __init__(self, df, covars):
        X = design(df, covars)
        self.g = pd.factorize(df["hosp"].values)[0]
        self.H = self.g.max() + 1
        self.G = sp.csr_matrix((np.ones(len(df)), (np.arange(len(df)), self.g)), shape=(len(df), self.H))
        Xd = X - X.groupby(self.g).transform("mean")
        Xd = Xd[Xd.columns[Xd.abs().sum() > 1e-9]]
        Xd = drop_collinear(Xd)
        self.names = list(Xd.columns)
        self.Xdm = Xd.to_numpy()                       # demeaned (for LPM)
        self.X = X[self.names].to_numpy()              # raw (for FE-GLM)
        self.df = df


def lpm(D, y):
    yv = D.df[y].values
    ybar = np.bincount(D.g, yv) / np.bincount(D.g)
    yd = yv - ybar[D.g]
    XtX = D.Xdm.T @ D.Xdm
    b = np.linalg.solve(XtX, D.Xdm.T @ yd)
    e = yd - D.Xdm @ b
    S = D.G.T @ (D.Xdm * e[:, None])
    n, k, H = len(yv), len(b), D.H
    c = H / (H - 1) * (n - 1) / (n - k)
    V = c * np.linalg.solve(XtX, np.linalg.solve(XtX, S.T @ S).T)
    fitted = ybar[D.g] + (D.X - (D.G.T @ D.X / np.bincount(D.g)[:, None])[D.g]) @ b
    return dict(b=b[0], se=np.sqrt(V[0, 0]), fitted=fitted)


def fe_glm(D, y, fam, rows=None, b0=None, maxit=60):
    """Logit or Poisson with hospital fixed effects; alpha profiled out."""
    yv_all = D.df[y].values
    g_all = D.g if rows is None else D.g[rows]
    X_all = D.X if rows is None else D.X[rows]
    yv_all = yv_all if rows is None else yv_all[rows]
    nh, sy = np.bincount(g_all, minlength=D.H), np.bincount(g_all, yv_all, minlength=D.H)
    ok_h = (sy > 0) & (sy < nh) if fam == "logit" else (sy > 0)
    ix = ok_h[g_all]
    Xf, yv, g = X_all[ix], yv_all[ix], g_all[ix]
    # drop dummy columns that perfectly predict the outcome (separation), never medicaid
    keep = np.ones(Xf.shape[1], bool)
    for j in range(1, Xf.shape[1]):
        on = Xf[:, j] != 0
        if on.sum() == 0 or yv[on].sum() == 0 or (fam == "logit" and yv[on].sum() == on.sum()):
            keep[j] = False
    Xf = Xf[:, keep]
    k = Xf.shape[1]
    G = sp.csr_matrix((np.ones(len(yv)), (np.arange(len(yv)), g)), shape=(len(yv), D.H))
    nh, sy = np.bincount(g, minlength=D.H), np.bincount(g, yv, minlength=D.H)
    b = np.zeros(k) if b0 is None or len(b0) != k else b0.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = sy / np.where(nh > 0, nh, 1)
        alpha = np.log(rate / (1 - rate)) if fam == "logit" else np.log(np.where(rate > 0, rate, 1))
    alpha = np.nan_to_num(alpha)

    def profile(b, alpha):
        xb = Xf @ b
        if fam == "poisson":
            den = np.bincount(g, np.exp(xb), minlength=D.H)
            with np.errstate(divide="ignore"):
                alpha = np.where(sy > 0, np.log(np.where(sy > 0, sy, 1)) - np.log(den), 0)
        else:
            # fixed-point update alpha += log(observed / expected events): stable for rare outcomes
            # (no division by p(1-p)), then Newton polish; converges to sum_h p = sum_h y.
            for it_a in range(500):
                p = expit(alpha[g] + xb)
                ep = np.bincount(g, p, minlength=D.H)
                if it_a < 200:
                    step = np.log(np.maximum(sy, 1e-12)) - np.log(np.maximum(ep, 1e-300))
                    step = np.where(sy > 0, step, 0)
                else:
                    step = (sy - ep) / np.maximum(np.bincount(g, p * (1 - p), minlength=D.H), 1e-12)
                alpha = alpha + np.clip(step, -3, 3)
                if np.abs(sy - ep).max() < 1e-8 * max(1.0, sy.max()):
                    break
        eta = alpha[g] + xb
        ll = (yv * eta - np.logaddexp(0, eta)).sum() if fam == "logit" else (yv * eta - np.exp(eta)).sum()
        return alpha, eta, ll

    alpha, eta, ll = profile(b, alpha)
    conv, singular = False, False
    for it in range(maxit):
        mu = expit(eta) if fam == "logit" else np.exp(eta)
        w = mu * (1 - mu) if fam == "logit" else mu
        xbar = (G.T @ (Xf * w[:, None])) / np.maximum(np.bincount(g, w, minlength=D.H), 1e-300)[:, None]
        Xt = Xf - xbar[g]
        score = Xt.T @ (yv - mu)
        Hs = Xt.T @ (Xt * w[:, None])
        try:
            step = np.linalg.solve(Hs, score)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(Hs, score, rcond=None)[0]
            singular = True
        t, ok = 1.0, False
        while t >= 1e-6:
            a2, e2, ll2 = profile(b + t * step, alpha)
            if np.isfinite(ll2) and ll2 >= ll - 1e-8:
                ok = True
                break
            t /= 2
        if not ok:            # no improving step: at the optimum if the Newton step is already tiny
            conv = (not singular) and np.abs(step).max() < 1e-4
            break
        b, alpha, eta, ll = b + t * step, a2, e2, ll2
        if np.abs(t * step).max() < 1e-8:
            conv = not singular
            break
    mu = expit(eta) if fam == "logit" else np.exp(eta)
    w = mu * (1 - mu) if fam == "logit" else mu
    xbar = (G.T @ (Xf * w[:, None])) / np.maximum(np.bincount(g, w, minlength=D.H), 1e-300)[:, None]
    Xt = Xf - xbar[g]
    Hs = Xt.T @ (Xt * w[:, None])
    S = G.T @ (Xt * (yv - mu)[:, None])
    Hi = np.linalg.pinv(Hs)
    Hn = int(ok_h.sum())
    V = Hn / (Hn - 1) * Hi @ (S.T @ S) @ Hi

    # marginal standardization over ALL stays in the (sub)sample; hospitals with no
    # within-hospital variation keep their observed (constant) probability under both payers
    xm = Xf[:, 0]
    eta1, eta0 = eta + b[0] * (1 - xm), eta - b[0] * xm
    p1, p0 = (expit(eta1), expit(eta0)) if fam == "logit" else (np.exp(eta1), np.exp(eta0))
    w1, w0 = (p1 * (1 - p1), p0 * (1 - p0)) if fam == "logit" else (p1, p0)
    N = len(yv_all)
    const = yv_all[~ix].sum()            # all-0 or all-1 hospitals: p1 = p0 = observed
    A1, A0 = (p1.sum() + const) / N, (p0.sum() + const) / N
    g1 = Xt.T @ w1 / N
    g1[0] += (w1 * (1 - xm)).sum() / N
    g0 = Xt.T @ w0 / N
    g0[0] -= (w0 * xm).sum() / N
    gd, gl = g1 - g0, g1 / A1 - g0 / A0
    se_d, se_l = np.sqrt(gd @ V @ gd), np.sqrt(gl @ V @ gl)
    full_mu = np.full(N, np.nan)
    full_mu[ix] = mu
    full_mu[~ix] = yv_all[~ix]
    return dict(b=b[0], se=np.sqrt(V[0, 0]), conv=conv, iters=it + 1, k=k, dropped_cols=int((~keep).sum()),
                hosp_used=Hn, A1=A1, A0=A0, rd=A1 - A0, rd_se=se_d, rr=A1 / A0, lrr_se=se_l,
                mu=full_mu, b_all=b, S=S, Hi=Hi, ix=ix)


def fit_logit(D, y, label=""):
    """FE-logit with a fit check (mean fitted = observed); refit from FE-Poisson start values if needed."""
    yv = D.df[y].values
    L = fe_glm(D, y, "logit")
    if (not L["conv"]) or abs(np.nanmean(L["mu"]) - yv.mean()) > 1e-4:
        say(f"    FE-logit {label}: first fit failed the check; refitting from FE-Poisson start values")
        L = fe_glm(D, y, "logit", b0=fe_glm(D, y, "poisson")["b_all"])
        if (not L["conv"]) or abs(np.nanmean(L["mu"]) - yv.mean()) > 1e-4:
            say(f"    WARNING FE-logit {label} still fails (mean fitted {100*np.nanmean(L['mu']):.3f}% vs observed {100*yv.mean():.3f}%)")
    return L


def evalue(rr, lo, hi):
    def ev(r):
        r = 1 / r if r < 1 else r
        return r + np.sqrt(r * (r - 1))
    lim = lo if rr >= 1 else hi
    ci = 1.0 if (lo <= 1 <= hi) else ev(lim)
    return ev(rr), ci


# ---------------------------------------------------------------- 1. bounded models, ladder
rows, ev_rows, keep_fit = [], [], {}
for mk, cov in MODELS.items():
    D = Design(P, cov)
    say(f"\n{MLAB[mk]}: {len(D.names) - 1} covariate columns  [{time.time() - T0:.0f}s]")
    L = lpm(D, "y_los")
    rows.append(dict(model=MLAB[mk], key=mk, outcome="LOS, days", method="Linear model, days",
                     est=L["b"], lo=L["b"] - 1.96 * L["se"], hi=L["b"] + 1.96 * L["se"]))
    for y in OUTS:
        L = lpm(D, y)
        rows.append(dict(model=MLAB[mk], key=mk, outcome=LAB[y], method="LPM (probability difference)",
                         est=100 * L["b"], lo=100 * (L["b"] - 1.96 * L["se"]), hi=100 * (L["b"] + 1.96 * L["se"])))
        Lg = fit_logit(D, y, f"{mk} {LAB[y]}")
        rd, se = Lg["rd"], Lg["rd_se"]
        rows.append(dict(model=MLAB[mk], key=mk, outcome=LAB[y], method="FE-logit, standardized probability difference",
                         est=100 * rd, lo=100 * (rd - 1.96 * se), hi=100 * (rd + 1.96 * se),
                         private_adj=100 * Lg["A0"], medicaid_adj=100 * Lg["A1"], converged=Lg["conv"],
                         hospitals=Lg["hosp_used"], dropped_cols=Lg["dropped_cols"]))
        rr_lo, rr_hi = Lg["rr"] * np.exp(-1.96 * Lg["lrr_se"]), Lg["rr"] * np.exp(1.96 * Lg["lrr_se"])
        rows.append(dict(model=MLAB[mk], key=mk, outcome=LAB[y], method="FE-logit, standardized probability ratio",
                         est=Lg["rr"], lo=rr_lo, hi=rr_hi, converged=Lg["conv"]))
        rows.append(dict(model=MLAB[mk], key=mk, outcome=LAB[y], method="FE-logit, conditional odds ratio",
                         est=np.exp(Lg["b"]), lo=np.exp(Lg["b"] - 1.96 * Lg["se"]), hi=np.exp(Lg["b"] + 1.96 * Lg["se"])))
        if mk in ("M0", "M3", "M3p", "M4"):
            Pg = fe_glm(D, y, "poisson")
            prr, plo, phi = np.exp(Pg["b"]), np.exp(Pg["b"] - 1.96 * Pg["se"]), np.exp(Pg["b"] + 1.96 * Pg["se"])
            rows.append(dict(model=MLAB[mk], key=mk, outcome=LAB[y], method="FE-Poisson, conditional risk ratio",
                             est=prr, lo=plo, hi=phi, converged=Pg["conv"], hospitals=Pg["hosp_used"]))
            for src, (r, lo, hi) in [("FE-logit standardized probability ratio", (Lg["rr"], rr_lo, rr_hi)),
                                     ("FE-Poisson conditional risk ratio", (prr, plo, phi))]:
                e_pt, e_ci = evalue(r, lo, hi)
                ev_rows.append(dict(model=MLAB[mk], key=mk, outcome=LAB[y], ratio=src,
                                    direction="Medicaid vs private (private = reference)",
                                    rr=r, lo=lo, hi=hi, evalue_point=e_pt, evalue_ci=e_ci))
            if mk == "M3":
                keep_fit[y] = dict(lpm=L["fitted"], logit=Lg["mu"], poisson=Pg["mu"], Lg=Lg, D=D)
        r = [x for x in rows if x["key"] == mk and x["outcome"] == LAB[y]]
        say(f"  {LAB[y]:<24} LPM {r[0]['est']:6.2f} | FE-logit RD {r[1]['est']:6.2f} ({r[1]['lo']:.2f}, {r[1]['hi']:.2f})"
            f" ratio {r[2]['est']:.2f}" + (f" | Poisson RR {r[4]['est']:.2f}" if len(r) > 4 else ""))
BOUNDED = pd.DataFrame(rows)
EVAL = pd.DataFrame(ev_rows)

# ---------------------------------------------------------------- 2. calibration and fit (M3)
cal, dist, hd, fs = [], [], [], []
med = P["medicaid"].values == 1
for y, F in keep_fit.items():
    yv = P[y].values
    for meth in ["lpm", "logit", "poisson"]:
        f = F[meth]
        fs.append(dict(outcome=LAB[y], model=meth, brier=np.mean((f - yv) ** 2), pct_below_0=100 * np.mean(f < 0),
                       pct_above_1=100 * np.mean(f > 1), mean_fitted=100 * f.mean(), observed=100 * yv.mean()))
        for lab, s in [("Medicaid", med), ("Private", ~med)]:
            q = np.percentile(f[s], [1, 10, 50, 90, 99])
            dist.append(dict(outcome=LAB[y], model=meth, payer=lab, pct_below_0=100 * np.mean(f[s] < 0),
                             pct_above_1=100 * np.mean(f[s] > 1), p1=100 * q[0], p10=100 * q[1], p50=100 * q[2],
                             p90=100 * q[3], p99=100 * q[4]))
            hm = pd.Series(f[s]).groupby(P["hosp"].values[s]).mean()
            hd.append(dict(outcome=LAB[y], model=meth, payer=lab, hospitals=len(hm),
                           hosp_p10=100 * hm.quantile(.1), hosp_median=100 * hm.median(), hosp_p90=100 * hm.quantile(.9)))
        if meth in ("lpm", "logit"):
            dec = pd.qcut(pd.Series(f).rank(method="first"), 10, labels=False)
            for lab, s in [("All", np.ones(len(f), bool)), ("Medicaid", med), ("Private", ~med)]:
                tab = pd.DataFrame(dict(dec=dec[s], f=f[s], y=yv[s])).groupby("dec").agg(n=("y", "size"), fitted=("f", "mean"), observed=("y", "mean"))
                for dd, r in tab.iterrows():
                    cal.append(dict(outcome=LAB[y], model=meth, payer=lab, decile=int(dd) + 1, n=r["n"],
                                    mean_fitted=100 * r["fitted"], observed=100 * r["observed"]))
CAL = pd.DataFrame(cal)
CAL["n"] = supp(CAL["n"])
DIST, HD, FITS = pd.DataFrame(dist), pd.DataFrame(hd), pd.DataFrame(fs)
say("\nFIT (M3): share of fitted values outside 0-100% and Brier score")
say(FITS.round(3).to_string(index=False))

# ---------------------------------------------------------------- 3. influence (FE-logit, M3)
inf = []
big = P["hosp"].value_counts().index[:10]
D_nb = Design(P[~P["hosp"].isin(big)].reset_index(drop=True), MODELS["M3"])
for y in ["y_snf", "y_irf", "y_ltch"]:
    Lg = keep_fit[y]["Lg"]
    dfb = -(Lg["S"] @ Lg["Hi"].T)[:, 0]       # one-step change in b when a hospital is removed
    dfb = dfb[np.abs(Lg["S"]).sum(1) > 0]
    bl = Lg["b"] + dfb
    nb = fit_logit(D_nb, y, f"excl-10 {LAB[y]}")
    inf.append(dict(outcome=LAB[y], or_full=np.exp(Lg["b"]), or_loo_min=np.exp(bl.min()), or_loo_max=np.exp(bl.max()),
                    rd_full=100 * Lg["rd"], rd_excl10=100 * nb["rd"], rd_excl10_lo=100 * (nb["rd"] - 1.96 * nb["rd_se"]),
                    rd_excl10_hi=100 * (nb["rd"] + 1.96 * nb["rd_se"])))
INF = pd.DataFrame(inf)
say("\nINFLUENCE (FE-logit, M3)"); say(INF.round(3).to_string(index=False))
del D_nb

# ---------------------------------------------------------------- 4. sub-cohorts
P["icu_type"] = np.select([P["icu_chg"] & ~P["ccu_chg"], ~P["icu_chg"] & P["ccu_chg"]], ["ICU only", "CCU only"], "ICU and CCU")
psych_mdc = P["apr_mdc"].astype(str).isin(["19", "20"])
psych_hosp = P["fac_psych"].fillna(False).astype(bool) if "fac_psych" in P else pd.Series(False, index=P.index)
subs = {"ICU charges only (020X)": P["icu_type"] == "ICU only",
        "CCU charges only (021X)": P["icu_type"] == "CCU only",
        "ICU and CCU charges": P["icu_type"] == "ICU and CCU",
        "ICU/CCU was the unit with most days": P["icu_spec1"].astype(bool),
        "Excluding mental-health / substance-use MDC (19, 20)": ~psych_mdc,
        "Excluding psychiatric-designated hospitals and MDC 19-20": ~psych_mdc & ~psych_hosp}
sc = []
for name, s in subs.items():
    sub = P[s.values].copy()
    sub = sub[sub.groupby("hosp")["hosp"].transform("size") > 1].reset_index(drop=True)
    nM, nP = int(sub["medicaid"].sum()), int((1 - sub["medicaid"]).sum())
    base = dict(subcohort=name, n_medicaid=supp(nM).item(), n_private=supp(nP).item(), hospitals=sub["hosp"].nunique())
    if min(nM, nP) < 500:
        sc.append(dict(base, note="too few stays for estimation"))
        continue
    for mk in ["M0", "M3"]:
        Ds = Design(sub, MODELS[mk])
        for y in ["y_snf", "y_irf", "y_ltch", "y_facility"]:
            L, Lg = lpm(Ds, y), fit_logit(Ds, y, f"{name} {mk} {LAB[y]}")
            sc.append(dict(base, model=mk, outcome=LAB[y],
                           medicaid_pct=100 * sub.loc[sub["medicaid"] == 1, y].mean(),
                           private_pct=100 * sub.loc[sub["medicaid"] == 0, y].mean(),
                           lpm=100 * L["b"], lpm_lo=100 * (L["b"] - 1.96 * L["se"]), lpm_hi=100 * (L["b"] + 1.96 * L["se"]),
                           fe_logit_rd=100 * Lg["rd"], fe_logit_lo=100 * (Lg["rd"] - 1.96 * Lg["rd_se"]),
                           fe_logit_hi=100 * (Lg["rd"] + 1.96 * Lg["rd_se"])))
    say(f"  sub-cohort {name}: Medicaid {nM:,}, private {nP:,}")
SUBC = pd.DataFrame(sc)

# ---------------------------------------------------------------- 5. POA coverage
poa = []
for lab, s in [("Medicaid", med), ("Private", ~med)]:
    q = P[s]
    poa.append(dict(payer=lab, pct_poa_reported=100 * q["poa_reported"].mean(),
                    mean_cci_all=q["cci"].mean(), mean_cci_poa=q["cci_poa"].mean(),
                    pct_cci_differs=100 * (q["cci"] != q["cci_poa"]).mean()))
POA = pd.DataFrame(poa)
say("\nPOA"); say(POA.round(2).to_string(index=False))

# ---------------------------------------------------------------- 6. optional bootstrap
BT = pd.DataFrame()
if BOOT:
    rng = np.random.default_rng(SEED)
    hosps = P["hosp"].unique()
    idx_by_h = P.groupby("hosp").indices
    bt = []
    for mk in ["M0", "M3"]:
        for y in ["y_snf", "y_irf", "y_ltch"]:
            ref = [r for r in rows if r["key"] == mk and r["outcome"] == LAB[y] and r["method"].startswith("FE-logit, standardized probability difference")][0]
            est = []
            for bi in range(BOOT):
                draw = rng.choice(hosps, len(hosps), replace=True)
                parts = [idx_by_h[h] for h in draw]
                B = P.iloc[np.concatenate(parts)].copy()
                B["hosp"] = np.repeat(np.arange(len(draw)), [len(p) for p in parts])   # duplicates = new hospitals
                try:
                    fb = fe_glm(Design(B.reset_index(drop=True), MODELS[mk]), y, "logit")
                    if fb["conv"]:
                        est.append(100 * fb["rd"])
                except Exception as ex:
                    say(f"    replicate {bi} failed: {ex}")
            est = np.array(est)
            bt.append(dict(model=mk, outcome=LAB[y], est=ref["est"], delta_lo=ref["lo"], delta_hi=ref["hi"],
                           boot_lo=np.percentile(est, 2.5), boot_hi=np.percentile(est, 97.5), boot_sd=est.std(),
                           replicates_ok=len(est), requested=BOOT, seed=SEED))
            say(f"  bootstrap {mk} {LAB[y]}: {len(est)}/{BOOT} ok, 95% {bt[-1]['boot_lo']:.2f} to {bt[-1]['boot_hi']:.2f}")
    BT = pd.DataFrame(bt)

with pd.ExcelWriter(os.path.join(OUT, "tx_bounded.xlsx")) as xw:
    for nm, df in [("flow_by_payer", FLOW), ("bounded", BOUNDED), ("evalues", EVAL), ("calibration", CAL), ("fitted_dist", DIST),
                   ("fitted_hosp", HD), ("fit_stats", FITS), ("influence", INF), ("subcohorts", SUBC),
                   ("poa", POA), ("boot", BT)]:
        df.to_excel(xw, sheet_name=nm, index=False)
with open(os.path.join(OUT, "tx_bounded_summary.txt"), "w") as fh:
    fh.write("\n".join(log))
say(f"\nWrote tx_output/tx_bounded.xlsx and tx_bounded_summary.txt (aggregate only). {time.time() - T0:.0f}s")
