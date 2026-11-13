"""
01_build_cohort.py - build the adult ICU-stay analytic file from the Texas Inpatient PUDF.

Run:   python 01_build_cohort.py "/path/to/your/Texas PUDF folder"  [first_year last_year]
       (default years 2017 2020)

Reads Base Data #1, Base Data #2 and the Facility Type Indicator file for every quarter,
keeps adult hospitalizations with ICU or CCU accommodation charges (or an ICU/CCU
specialty-unit code), derives the analysis variables and drops the raw diagnosis and
procedure codes.

Writes to ./tx_output/ :
  tx_cohort.pkl              RECORD-LEVEL. Stays on this computer (Data Use Agreement).
  tx_all_discharges.csv      aggregate counts of all discharges by year (flow diagram top box)
  tx_build_log.txt           files read, row counts, duplicate checks (aggregate only)
"""
import os
import sys
import time

import numpy as np
import pandas as pd

import tx_common as C
from tx_files import find_files, read_table

data_dir = sys.argv[1] if len(sys.argv) > 1 else "."
Y0, Y1 = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (2017, 2020)
OUT = "tx_output"
os.makedirs(OUT, exist_ok=True)
log_lines = []


def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)


entries = find_files(data_dir)

# DSHS correction list: outpatient records that were released inside the 2Q2020 inpatient file.
# (The 3Q2020 addendum lists 100 inpatient records that went to the OUTPATIENT file instead;
#  they cannot be recovered from the inpatient PUDF and are noted as a limitation.)
EXCLUDE_IDS = set()
for root, _, files in os.walk(data_dir):
    for f in files:
        low = f.lower()
        if low.endswith((".xlsx", ".xls")) and "outpatient" in low and "inpatient" in low and "q22020" in low:
            x = pd.read_excel(os.path.join(root, f), dtype=str)
            col = next((c for c in x.columns if str(c).strip().upper() == "RECORD_ID"), x.columns[0])
            ids = x[col].dropna().astype(str).str.strip().str.lstrip("0")
            EXCLUDE_IDS |= set(ids[ids != ""])
            log(f"Correction list {f}: {len(ids):,} RECORD_IDs to remove (outpatient records in 2Q2020 file)")
# A quarter's files may also hold late discharges from the previous quarter, so read every
# quarter from Y0Q1 to (Y1+1)Q1 if present and select on the DISCHARGE field itself.
quarters = sorted({(e["year"], e["qtr"]) for e in entries
                   if e["kind"] == "base1" and e["year"] and Y0 <= e["year"] <= Y1 + 1})
log(f"Base #1 quarters found: {', '.join(f'{y}Q{q}' for y, q in quarters)}")


def pick(kind, y, q):
    xs = [e for e in entries if e["kind"] == kind and e["year"] == y and e["qtr"] == q]
    return xs[0] if xs else None


def to_num(s):
    return pd.to_numeric(s.str.replace(",", "", regex=False), errors="coerce")


def derive(df, icu_ids, ccu_ids=frozenset()):
    """Turn a Base #1 chunk into analysis rows (adult ICU stays only)."""
    out = pd.DataFrame(index=df.index)
    out["rid"] = df["RECORD_ID"]
    out["discharge_q"] = df["DISCHARGE"].str.upper()
    out["year"] = pd.to_numeric(out["discharge_q"].str[:4], errors="coerce")
    out["hosp"] = df["THCIC_ID"]
    out["pay1"] = df["FIRST_PAYMENT_SRC"].map(C.norm2)
    out["pay2"] = df["SECONDARY_PAYMENT_SRC"].map(C.norm2)
    out["ins_cat"] = out["pay1"].map(C.payer_group)
    out["sec_medicaid"] = out["pay2"].isin(C.MEDICAID)
    out["age_code"] = df["PAT_AGE"].map(C.norm2)
    out["age_group"] = out["age_code"].map(C.AGE_GROUP).fillna("invalid")
    out["age_masked"] = out["age_code"].isin({"22", "23", "24", "25", "26"})
    out["sex"] = df["SEX_CODE"].str.upper().where(df["SEX_CODE"].str.upper().isin(["M", "F"]), "U")
    out["race_cat"] = df["RACE"].map({"1": "AIAN", "2": "Asian/PI", "3": "Black",
                                      "4": "White", "5": "Other"}).fillna("Unknown")
    out["eth_cat"] = df["ETHNICITY"].map({"1": "Hispanic", "2": "Not Hispanic"}).fillna("Unknown")
    out["adm_type_cat"] = df["TYPE_OF_ADMISSION"].map(
        {"1": "Emergency", "2": "Urgent", "3": "Elective", "5": "Trauma"}).fillna("Other/unknown")
    out["adm_src_cat"] = df["SOURCE_OF_ADMISSION"].str.upper().map(
        {"1": "Non-health-care", "2": "Clinic/physician", "4": "Transfer: hospital",
         "5": "Transfer: SNF/ICF/ALF", "6": "Transfer: other facility",
         "D": "Transfer: other facility", "F": "Transfer: other facility"}).fillna("Other/unknown")
    out["ed_flag"] = df["EMERGENCY_DEPT_FLAG"].str.upper().eq("Y")
    out["status"] = df["PAT_STATUS"].map(C.norm2)
    out["dc_group"] = out["status"].map(C.STATUS_GROUP).fillna("invalid")
    out["dc_ama"] = out["status"].isin(C.AMA_CODES)
    out["dc_acute_transfer"] = out["status"].isin(C.ACUTE_TRANSFER_CODES)
    out["dc_medicaid_nf"] = out["status"].isin(C.MEDICAID_NF_CODES)
    out["los"] = pd.to_numeric(df["LENGTH_OF_STAY"], errors="coerce")
    out["type_of_bill"] = df["TYPE_OF_BILL"]
    out["apr_mdc"] = df["APR_MDC"].map(C.norm2).replace("", "NA")
    out["apr_sev"] = df["ILLNESS_SEVERITY"].where(df["ILLNESS_SEVERITY"].isin(list("1234")), "0")
    out["apr_rom"] = df["RISK_MORTALITY"].where(df["RISK_MORTALITY"].isin(list("1234")), "0")
    spec = df[[f"SPEC_UNIT_{i}" for i in range(1, 6)]].apply(lambda s: s.str.upper())
    out["icu_spec"] = spec.isin(["I", "C"]).any(axis=1)
    out["icu_spec1"] = spec["SPEC_UNIT_1"].isin(["I", "C"])    # ICU/CCU = unit with most days
    out["icu_chg"] = df["RECORD_ID"].isin(icu_ids)      # ICU accommodation charges (020X)
    out["ccu_chg"] = df["RECORD_ID"].isin(ccu_ids)      # coronary care charges (021X)
    out["icu_rev"] = out["icu_chg"] | out["ccu_chg"]

    # keep adult ICU stays before the expensive code scans
    keep = (out["icu_rev"] | out["icu_spec"]) & out["age_code"].isin(C.AGE_18_64 | C.AGE_65_PLUS)
    out, d = out[keep].copy(), df[keep]

    proc = d[C.PROC_COLS].apply(lambda s: s.str.upper().str.replace(".", "", regex=False))
    out["vent_any"] = proc.isin(C.VENT_ANY).any(axis=1)
    out["vent96"] = proc.isin(C.VENT_96).any(axis=1)

    dx = d[C.DX_COLS].apply(lambda s: s.str.upper().str.replace(".", "", regex=False))
    long = dx.stack()
    long = long[long != ""]

    def any_match(rx):
        hit = long.str.match(rx)
        return hit.groupby(level=0).any().reindex(out.index, fill_value=False)

    def cci_from(match):
        f = pd.DataFrame({k: match(rx) for k, rx in C.CHARLSON_RE.items()})
        f.loc[f["sev_liver"], "mild_liver"] = False
        f.loc[f["dm_comp"], "dm"] = False
        f.loc[f["mets"], "cancer"] = False
        return sum(f[k].astype(int) * w for k, w in C.CHARLSON_W.items())

    out["cci"] = cci_from(any_match)

    # Charlson from diagnoses flagged present on admission only (POA = Y, W, or 1 = exempt)
    poa = d[C.POA_COLS].apply(lambda s: s.fillna("").str.strip().str.upper())
    poa.columns = C.DX_COLS
    poa_long = poa.stack().reindex(long.index).fillna("")
    long_poa = long[poa_long.isin(C.POA_YES)]
    out["poa_reported"] = (poa.isin(["Y", "N", "U", "W", "1"])).any(axis=1)

    def any_match_poa(rx):
        hit = long_poa.str.match(rx)
        return hit.groupby(level=0).any().reindex(out.index, fill_value=False)

    out["cci_poa"] = cci_from(any_match_poa)
    out["housing_econ_icd"] = any_match(C.RE_HOUSING)
    out["sud_icd"] = any_match(C.RE_SUD)
    out["smi_icd"] = any_match(C.RE_SMI)
    return out


t0 = time.time()
parts, all_counts, FACS = [], [], []
for (y, q) in quarters:
    b1, b2, fac = pick("base1", y, q), pick("base2", y, q), pick("facility", y, q)
    if b2 is None:
        log(f"WARNING {y}Q{q}: Base #2 file missing - ICU charge flag unavailable; "
            "only the specialty-unit ICU definition can be used for this quarter.")
        icu_ids, ccu_ids = set(), set()
    else:
        b2df, miss = read_table(b2, C.BASE2_KEEP, default_columns=C.BASE2_COLUMNS)
        icu = (to_num(b2df["ICU_AMOUNT"]).fillna(0) > 0) | (to_num(b2df["CCU_AMOUNT"]).fillna(0) > 0)
        icu_ids = set(b2df.loc[to_num(b2df["ICU_AMOUNT"]).fillna(0) > 0, "RECORD_ID"])
        ccu_ids = set(b2df.loc[to_num(b2df["CCU_AMOUNT"]).fillna(0) > 0, "RECORD_ID"])
        log(f"{y}Q{q} Base #2: {len(b2df):,} records, {icu.sum():,} with ICU/CCU charges"
            + (f" (missing columns {miss})" if miss else ""))
        del b2df

    chunks, miss = read_table(b1, C.BASE1_KEEP, chunksize=200_000)
    if miss:
        log(f"  NOTE {y}Q{q} Base #1 is missing columns {miss}; they are treated as blank.")
    n_rows = 0
    n_removed = 0
    for ch in chunks:
        if EXCLUDE_IDS:
            bad = ch["RECORD_ID"].str.lstrip("0").isin(EXCLUDE_IDS)
            n_removed += int(bad.sum())
            ch = ch[~bad]
        n_rows += len(ch)
        yr = pd.to_numeric(ch["DISCHARGE"].str[:4], errors="coerce")
        all_counts.append(pd.DataFrame({"year": yr, "rid": ch["RECORD_ID"]}))
        part = derive(ch, icu_ids, ccu_ids)
        part["file_q"] = f"{y}Q{q}"
        parts.append(part)
    log(f"{y}Q{q} Base #1: {n_rows:,} records read"
        + (f", {n_removed:,} removed per DSHS correction list" if n_removed else "")
        + f" ({time.time() - t0:,.0f}s elapsed)")

    if fac is not None:
        fdf, _ = read_table(fac, C.FACILITY_KEEP, default_columns=C.FACILITY_COLUMNS)
        fdf["file_q"] = f"{y}Q{q}"
        FACS.append(fdf)

cohort = pd.concat(parts, ignore_index=True)

# all-discharge counts (unique RECORD_ID within discharge year), for the flow diagram
allc = pd.concat(all_counts, ignore_index=True).drop_duplicates("rid")
allc = allc[allc["year"].between(Y0, Y1)].groupby("year").size().rename("all_discharges")
allc.to_csv(os.path.join(OUT, "tx_all_discharges.csv"))
log(f"\nAll discharges by year:\n{allc.to_string()}")

# duplicates across quarterly files (same RECORD_ID) - keep the first occurrence
dups = cohort.duplicated("rid").sum()
log(f"Duplicate RECORD_IDs across quarterly files in the ICU file: {dups:,} (dropped)")
cohort = cohort.drop_duplicates("rid")
cohort = cohort[cohort["year"].between(Y0, Y1)].copy()

# facility characteristics: take each hospital's row from the quarter file it came from
if FACS:
    fac_all = pd.concat(FACS, ignore_index=True).drop_duplicates(["THCIC_ID", "file_q"])
    fac_all = fac_all.rename(columns={"THCIC_ID": "hosp"})
    fac_all["teaching"] = fac_all["FAC_TEACHING_IND"].str.upper().isin(["A", "X"])
    fac_all["acute_ind"] = fac_all["FAC_ACUTE_CARE_IND"].str.strip() != ""
    spec_cols = ["FAC_PSYCH_IND", "FAC_REHAB_IND", "FAC_LONG_TERM_AC_IND", "FAC_PEDS_IND",
                 "FAC_SNF_IND", "FAC_OTHER_LTC_IND"]
    fac_all["specialty_ind"] = (fac_all[spec_cols].apply(lambda s: s.str.strip() != "")).any(axis=1)
    fac_all["ltch_ind"] = fac_all["FAC_LONG_TERM_AC_IND"].str.strip() != ""
    fac_all["fac_psych"] = fac_all["FAC_PSYCH_IND"].str.strip() != ""
    cohort = cohort.merge(fac_all[["hosp", "file_q", "teaching", "acute_ind", "specialty_ind",
                                   "ltch_ind", "fac_psych", "CERT_STATUS"]], on=["hosp", "file_q"], how="left")
else:
    log("WARNING: no Facility Type Indicator files found; hospital-type exclusions skipped.")
    for c in ["teaching", "acute_ind", "specialty_ind", "ltch_ind", "fac_psych"]:
        cohort[c] = np.nan
    cohort["CERT_STATUS"] = ""

cohort["masked_hosp"] = cohort["hosp"].isin(C.MASKED_HOSPITAL_IDS)
# Hospitals to exclude from the primary sample: specialty hospitals without an acute-care
# indicator (rehab, LTCH, psychiatric, children's) whose "ICU" stays are not comparable.
cohort["non_acute_hosp"] = cohort["specialty_ind"].eq(True) & cohort["acute_ind"].eq(False)

# compact storage: low-cardinality text columns as categories (rid and hosp stay text)
for c in cohort.columns:
    if c not in ("rid", "hosp") and (cohort[c].dtype == object or str(cohort[c].dtype) in ("str", "string")):
        cohort[c] = cohort[c].astype("category")
log(f"Memory used by the analytic file: {cohort.memory_usage(deep=True).sum() / 1e6:,.0f} MB")

path = os.path.join(OUT, "tx_cohort.pkl")
cohort.to_pickle(path)
log(f"\nWrote {path}: {len(cohort):,} adult ICU-stay records "
    f"({cohort['icu_rev'].sum():,} by ICU/CCU charges, {cohort['icu_spec'].sum():,} by specialty unit). "
    "RECORD-LEVEL: keep on this computer only.")
log(f"Total time {time.time() - t0:,.0f}s")
with open(os.path.join(OUT, "tx_build_log.txt"), "w") as fh:
    fh.write("\n".join(log_lines))
