"""Synthetic Texas PUDF-layout files for testing the pipeline (no real data)."""
import os
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tx_common as C

rng = np.random.default_rng(7)
OUT = sys.argv[1] if len(sys.argv) > 1 else "synth_data"
os.makedirs(OUT, exist_ok=True)
H = [f"{100000 + i:06d}" for i in range(60)] + ["999999"]
hosp_effect = dict(zip(H, rng.normal(0, 0.4, len(H))))


def quarter(y, q, n):
    d = {c: [""] * n for c in C.BASE1_COLUMNS}
    rid = [f"{y}{q}{i:07d}" for i in range(n)]
    d["RECORD_ID"] = rid
    disc = np.where(rng.random(n) < 0.03, f"{y - (q == 1)}Q{4 if q == 1 else q - 1}", f"{y}Q{q}")
    d["DISCHARGE"] = disc
    hosp = rng.choice(H, n)
    d["THCIC_ID"] = hosp
    age = rng.choice(["05", "06", "08", "10", "12", "13", "14", "15", "16", "18", "20", "23", "24"], n)
    d["PAT_AGE"] = age
    medicaid = rng.random(n) < 0.3
    pay = np.where(medicaid, "MC", rng.choice(["12", "BL", "CI", "HM", "16", "MA", "ZZ", "VA"], n))
    d["FIRST_PAYMENT_SRC"] = pay
    d["SECONDARY_PAYMENT_SRC"] = np.where(rng.random(n) < 0.05, "MC", "")
    d["SEX_CODE"] = rng.choice(["M", "F", "U"], n, p=[.5, .48, .02])
    d["RACE"] = rng.choice(list("12345"), n, p=[.01, .05, .15, .6, .19])
    d["ETHNICITY"] = rng.choice(["1", "2", ""], n, p=[.35, .6, .05])
    d["TYPE_OF_ADMISSION"] = rng.choice(list("12359"), n, p=[.6, .15, .15, .08, .02])
    d["SOURCE_OF_ADMISSION"] = rng.choice(list("12456"), n, p=[.7, .1, .12, .04, .04])
    d["EMERGENCY_DEPT_FLAG"] = rng.choice(["Y", "N"], n)
    sev = rng.choice(list("1234"), n, p=[.15, .35, .3, .2])
    d["APR_MDC"] = rng.choice(["01", "04", "05", "06", "18", "21"], n)
    ung = rng.random(n) < 0.02                       # ungroupable APR-DRG records
    d["APR_MDC"] = np.where(ung, "", d["APR_MDC"])
    d["ILLNESS_SEVERITY"] = np.where(ung, "0", sev)
    d["RISK_MORTALITY"] = np.where(ung, "0", rng.choice(list("1234"), n))
    icu = rng.random(n) < 0.35
    d["SPEC_UNIT_1"] = np.where(icu & (rng.random(n) < 0.8), "I", "")
    vent = icu & (rng.random(n) < 0.35)
    d["PRINC_SURG_PROC_CODE"] = np.where(vent, rng.choice(["5A1935Z", "5A1945Z", "5A1955Z"], n), "0BH17EZ")
    dxpool = ["I214", "I509", "J449", "E119", "E1122", "N184", "C787", "F1020", "F200", "Z590",
              "K7031", "B20", "I10", "J9601", "A419"]
    for i, c in enumerate(C.DX_COLS[:8]):
        d[c] = np.where(rng.random(n) < 0.35, rng.choice(dxpool, n), "")
    lin = (-3.0 + 0.8 * medicaid + 0.5 * (sev.astype(int) - 2) + 0.4 * vent
           + np.vectorize(hosp_effect.get)(hosp))
    p_fac = 1 / (1 + np.exp(-lin))
    u = rng.random(n)
    status = np.where(u < p_fac * 0.6, rng.choice(["03", "64", "61"], n),
             np.where(u < p_fac * 0.8, "62",
             np.where(u < p_fac, "63",
             np.where(rng.random(n) < 0.06, "20",
             np.where(rng.random(n) < 0.03, "50",
             np.where(rng.random(n) < 0.15, "06",
             np.where(rng.random(n) < 0.05, rng.choice(["07", "02", "65", "70"], n), "01")))))))
    d["PAT_STATUS"] = status
    d["LENGTH_OF_STAY"] = (1 + rng.gamma(2, 2.5 + 1.0 * medicaid + vent * 3, n)).astype(int).astype(str)
    d["TYPE_OF_BILL"] = "111"
    b1 = pd.DataFrame(d)
    b2 = pd.DataFrame({c: "" for c in C.BASE2_COLUMNS}, index=range(n))
    b2["RECORD_ID"] = rid
    b2["ICU_AMOUNT"] = np.where(icu, (rng.random(n) * 20000).round(2).astype(str), "0")
    b2["CCU_AMOUNT"] = "0"
    fac = pd.DataFrame({c: "" for c in C.FACILITY_COLUMNS}, index=range(len(H)))
    fac["THCIC_ID"] = H
    fac["FAC_ACUTE_CARE_IND"] = ["X"] * (len(H) - 3) + ["", "", "X"]
    fac["FAC_LONG_TERM_AC_IND"] = [""] * (len(H) - 3) + ["X", "", ""]
    fac["FAC_REHAB_IND"] = [""] * (len(H) - 2) + ["X", ""]
    fac["FAC_TEACHING_IND"] = rng.choice(["A", "X", ""], len(H))
    fac["CERT_STATUS"] = "1"
    return b1, b2, fac


for (y, q) in [(2019, 1), (2019, 2), (2020, 1)]:
    b1, b2, fac = quarter(y, q, 30000)
    tag = f"{q}q{y}"
    names = {f"PUDF_base1_{tag}_tab.txt": b1, f"PUDF_base2_{tag}_tab.txt": b2,
             f"PUDF_Facility_type{tag}_tab.txt": fac}
    if (y, q) == (2019, 2):          # one quarter left zipped, like the download
        with zipfile.ZipFile(os.path.join(OUT, f"PUDF_{tag}_tab-delimited.zip"), "w") as z:
            for nm, df in names.items():
                z.writestr(nm, df.to_csv(sep="\t", index=False))
    else:
        for nm, df in names.items():
            df.to_csv(os.path.join(OUT, nm), sep="\t", index=False)
print("synthetic files written to", OUT)
