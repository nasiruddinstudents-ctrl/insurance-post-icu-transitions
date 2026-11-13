"""
tx_common.py - shared definitions for the Texas Inpatient PUDF replication
(Insurance and Post-ICU Transitions, external replication of the MIMIC-IV analysis).

Every code below comes from the DSHS/THCIC Texas Hospital Inpatient Discharge PUDF
User Manual (2019 edition, Document E25-14163). Check the manual for each year you use;
the 2017-2020 layouts are the same ICD-10 layout.

DATA USE AGREEMENT: the PUDF must stay on your own computer. These scripts write
record-level files only to your local working folder; everything meant to be shared
(pasted into a chat, put in a paper or a GitHub repo) is aggregate, with cells < 11
suppressed. Never upload the raw files or the cohort pickle anywhere.
"""
import re

# --------------------------------------------------------------------------------------
# Column order of the Base Data #1 file (167 fields), used only if a tab file has no header
# --------------------------------------------------------------------------------------
BASE1_COLUMNS = (
    ["RECORD_ID", "DISCHARGE", "THCIC_ID", "TYPE_OF_ADMISSION", "SOURCE_OF_ADMISSION"]
    + [f"SPEC_UNIT_{i}" for i in range(1, 6)]
    + ["PAT_STATE", "PAT_ZIP", "PAT_COUNTRY", "PAT_COUNTY", "PUBLIC_HEALTH_REGION",
       "PAT_STATUS", "SEX_CODE", "RACE", "ETHNICITY", "ADMIT_WEEKDAY", "LENGTH_OF_STAY",
       "PAT_AGE", "FIRST_PAYMENT_SRC", "SECONDARY_PAYMENT_SRC", "TYPE_OF_BILL",
       "TOTAL_CHARGES", "TOTAL_NON_COV_CHARGES", "TOTAL_CHARGES_ACCOMM",
       "TOTAL_NON_COV_CHARGES_ACCOMM", "TOTAL_CHARGES_ANCIL", "TOTAL_NON_COV_CHARGES_ANCIL",
       "ADMITTING_DIAGNOSIS", "PRINC_DIAG_CODE", "POA_PRINC_DIAG_CODE"]
    + [c for i in range(1, 25) for c in (f"OTH_DIAG_CODE_{i}", f"POA_OTH_DIAG_CODE_{i}")]
    + [c for i in range(1, 11) for c in (f"E_CODE_{i}", f"POA_E_CODE_{i}")]
    + ["PRINC_SURG_PROC_CODE", "PRINC_SURG_PROC_DAY"]
    + [c for i in range(1, 25) for c in (f"OTH_SURG_PROC_CODE_{i}", f"OTH_SURG_PROC_DAY_{i}")]
    + ["MS_MDC", "MS_DRG", "MS_GROUPER_VERSION_NBR", "MS_GROUPER_ERROR_CODE", "APR_MDC",
       "APR_DRG", "RISK_MORTALITY", "ILLNESS_SEVERITY", "APR_GROUPER_VERSION_NBR",
       "APR_GROUPER_ERROR_CODE", "ATTENDING_PHYSICIAN_UNIF_ID", "OPERATING_PHYSICIAN_UNIF_ID",
       "ENCOUNTER_INDICATOR", "PROVIDER_NAME", "EMERGENCY_DEPT_FLAG"]
)
assert len(BASE1_COLUMNS) == 167

_AMT = ["PRIVATE", "SEMI_PRIVATE", "WARD", "ICU", "CCU", "OTHER", "PHARM", "MEDSURG", "DME",
        "USED_DME", "PT", "OT", "SPEECH", "IT", "BLOOD", "BLOOD_ADM", "OR", "LITH", "CARD", "ANES",
        "LAB", "RAD", "MRI", "OP", "ER", "AMBULANCE", "PRO_FEE", "ORGAN", "ESRD", "CLINIC"]
BASE2_COLUMNS = (
    ["RECORD_ID"] + [f"{a}_AMOUNT" for a in _AMT]
    + [c for i in range(1, 13) for c in (f"OCCUR_CODE_{i}", f"OCCUR_DAY_{i}")]
    + [c for i in range(1, 5) for c in (f"OCCUR_SPAN_CODE_{i}", f"OCCUR_SPAN_FROM_{i}",
                                        f"OCCUR_SPAN_THRU_{i}")]
    + [f"CONDITION_CODE_{i}" for i in range(1, 9)]
    + [c for i in range(1, 13) for c in (f"VALUE_CODE_{i}", f"VALUE_AMOUNT_{i}")]
)
assert len(BASE2_COLUMNS) == 99
FACILITY_COLUMNS = ["THCIC_ID", "PROVIDER_NAME", "FAC_TEACHING_IND", "FAC_PSYCH_IND",
                    "FAC_REHAB_IND", "FAC_ACUTE_CARE_IND", "FAC_SNF_IND", "FAC_LONG_TERM_AC_IND",
                    "FAC_OTHER_LTC_IND", "FAC_PEDS_IND", "POA_PROVIDER_INDICATOR", "CERT_STATUS"]

DX_COLS = ["PRINC_DIAG_CODE"] + [f"OTH_DIAG_CODE_{i}" for i in range(1, 25)]
POA_COLS = ["POA_PRINC_DIAG_CODE"] + [f"POA_OTH_DIAG_CODE_{i}" for i in range(1, 25)]
POA_YES = {"Y", "W", "1"}          # present on admission, clinically undetermined, exempt
PROC_COLS = ["PRINC_SURG_PROC_CODE"] + [f"OTH_SURG_PROC_CODE_{i}" for i in range(1, 25)]

BASE1_KEEP = (
    ["RECORD_ID", "DISCHARGE", "THCIC_ID", "TYPE_OF_ADMISSION", "SOURCE_OF_ADMISSION"]
    + [f"SPEC_UNIT_{i}" for i in range(1, 6)]
    + ["PAT_STATUS", "SEX_CODE", "RACE", "ETHNICITY", "LENGTH_OF_STAY", "PAT_AGE",
       "FIRST_PAYMENT_SRC", "SECONDARY_PAYMENT_SRC", "TYPE_OF_BILL", "APR_MDC",
       "RISK_MORTALITY", "ILLNESS_SEVERITY", "EMERGENCY_DEPT_FLAG"]
    + DX_COLS + POA_COLS + PROC_COLS
)
BASE2_KEEP = ["RECORD_ID", "ICU_AMOUNT", "CCU_AMOUNT"]
FACILITY_KEEP = ["THCIC_ID", "FAC_TEACHING_IND", "FAC_PSYCH_IND", "FAC_REHAB_IND",
                 "FAC_ACUTE_CARE_IND", "FAC_SNF_IND", "FAC_LONG_TERM_AC_IND",
                 "FAC_OTHER_LTC_IND", "FAC_PEDS_IND", "CERT_STATUS"]

# Masked / aggregated hospital IDs (manual, Field 3): cannot be used as hospital fixed effects
MASKED_HOSPITAL_IDS = {"999999", "999998"}

# --------------------------------------------------------------------------------------
# Payer (FIRST_PAYMENT_SRC). X12 claim-filing-indicator codes.
# Note: 16 = "HMO Medicare Risk" (Medicare Advantage), HM = commercial HMO.
# --------------------------------------------------------------------------------------
MEDICAID = {"MC"}
PRIVATE = {"12", "13", "14", "15", "HM", "BL", "CI"}   # PPO, POS, EPO, indemnity, HMO, BCBS, commercial
MEDICARE = {"MA", "MB", "16"}                         # Part A, Part B, Medicare Advantage HMO


def payer_group(code):
    c = (code or "").strip().upper()
    if c in MEDICAID:
        return "Medicaid"
    if c in PRIVATE:
        return "Private"
    if c in MEDICARE:
        return "Medicare"
    if c in {"ZZ", "09"}:
        return "Self-pay/charity/unknown"
    if c == "" or c == "`":
        return "Missing/invalid"
    return "Other"            # VA, CHAMPUS, Title V, workers comp, liability, other federal, etc.


# --------------------------------------------------------------------------------------
# Discharge status (PAT_STATUS) -> analytic group. Planned-readmission variants (81-95)
# are folded into their base destination.
# --------------------------------------------------------------------------------------
STATUS_GROUP = {
    "01": "home", "81": "home",
    "06": "home_health", "86": "home_health",
    "03": "snf", "83": "snf", "61": "snf", "89": "snf",       # Medicare-certified SNF, swing bed
    "64": "snf", "92": "snf",                                  # Medicaid-only nursing facility
    "62": "irf", "90": "irf",
    "63": "ltch", "91": "ltch",
    "07": "other",                                             # AMA (S1 removes)
    "02": "other", "82": "other", "05": "other", "85": "other",  # acute / cancer / children's hosp.
    "66": "other", "94": "other", "43": "other", "88": "other",  # CAH, federal facility
    "04": "other", "84": "other",                              # custodial / supportive care
    "65": "other", "93": "other",                              # psychiatric
    "21": "other", "87": "other", "69": "other", "70": "other", "95": "other",
    "20": "died", "40": "died", "41": "died", "42": "died",
    "50": "hospice", "51": "hospice",
    "30": "still_patient", "09": "still_patient",
}
AMA_CODES = {"07"}
ACUTE_TRANSFER_CODES = {"02", "82", "05", "85", "66", "94", "43", "88"}
MEDICAID_NF_CODES = {"64", "92"}
DESTINATIONS = ["home", "home_health", "snf", "irf", "ltch", "other"]
FACILITY_DESTS = {"snf", "irf", "ltch"}

# --------------------------------------------------------------------------------------
# Age (PAT_AGE). 22-26 are the coarse groups used for HIV and drug/alcohol-use records.
# --------------------------------------------------------------------------------------
AGE_GROUP = {
    "06": "18-24", "07": "18-24", "08": "25-34", "09": "25-34", "10": "35-44", "11": "35-44",
    "12": "45-54", "13": "45-54", "14": "55-64", "15": "55-64",
    "23": "18-44 (masked)", "24": "45-64 (masked)",
    "16": "65-74", "17": "65-74", "25": "65-74 (masked)",
    "18": "75-84", "19": "75-84", "20": "85+", "21": "85+", "26": "75+ (masked)",
}
AGE_18_64 = {"06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "23", "24"}
AGE_65_PLUS = {"16", "17", "18", "19", "20", "21", "25", "26"}

# --------------------------------------------------------------------------------------
# Procedures and diagnoses
# --------------------------------------------------------------------------------------
VENT_ANY = {"5A1935Z", "5A1945Z", "5A1955Z"}
VENT_96 = {"5A1955Z"}

# Social-needs proxies: identical code groups to the MIMIC S4 model (03_aim1_s4_flags.sql)
RE_HOUSING = re.compile(r"^Z59")
RE_SUD = re.compile(r"^F(1[0-6]|18|19)")
RE_SMI = re.compile(r"^(F2|F30|F31|F32[23]|F33[23])")

# Charlson comorbidities, Quan et al. 2005 ICD-10 coding algorithm, adapted to ICD-10-CM
# prefixes (diabetes also covers E08, E09, E13). Weights: original Charlson; no age points.
CHARLSON = {
    "mi":        (1, r"^(I21|I22|I252)"),
    "chf":       (1, r"^(I099|I110|I130|I132|I255|I420|I42[5-9]|I43|I50|P290)"),
    "pvd":       (1, r"^(I70|I71|I731|I738|I739|I771|I790|I792|K551|K558|K559|Z958|Z959)"),
    "cvd":       (1, r"^(G45|G46|H340|I6)"),
    "dementia":  (1, r"^(F0[0-3]|F051|G30|G311)"),
    "cpd":       (1, r"^(I278|I279|J4[0-7]|J6[0-7]|J684|J701|J703)"),
    "rheum":     (1, r"^(M05|M06|M315|M3[2-4]|M351|M353|M360)"),
    "pud":       (1, r"^(K2[5-8])"),
    "mild_liver": (1, r"^(B18|K70[0-3]|K709|K71[3-5]|K717|K73|K74|K760|K76[2-4]|K768|K769|Z944)"),
    "dm":        (1, r"^(E(0[89]|1[0-4])[0169])"),
    "dm_comp":   (2, r"^(E(0[89]|1[0-4])[2-57])"),
    "hemi":      (2, r"^(G041|G114|G801|G802|G81|G82|G83[0-4]|G839)"),
    "renal":     (2, r"^(I120|I131|N03[2-7]|N05[2-7]|N18|N19|N250|Z49[0-2]|Z940|Z992)"),
    "cancer":    (2, r"^(C[01]\d|C2[0-6]|C3[0-4]|C3[7-9]|C4[01]|C43|C4[5-9]|C5[0-8]|C6\d|C7[0-6]|C8[1-5]|C88|C9[0-7])"),
    "sev_liver": (3, r"^(I850|I859|I864|I982|K704|K711|K721|K729|K76[5-7])"),
    "mets":      (6, r"^(C7[7-9]|C80)"),
    "hiv":       (6, r"^(B2[0-2]|B24)"),
}
CHARLSON_RE = {k: re.compile(p) for k, (_, p) in CHARLSON.items()}
CHARLSON_W = {k: w for k, (w, _) in CHARLSON.items()}


def charlson_score(flags):
    """flags: dict condition -> bool. Applies the usual hierarchy (severe over mild)."""
    f = dict(flags)
    if f.get("sev_liver"):
        f["mild_liver"] = False
    if f.get("dm_comp"):
        f["dm"] = False
    if f.get("mets"):
        f["cancer"] = False
    return sum(CHARLSON_W[k] for k, v in f.items() if v)


def norm2(x):
    """Normalise a 1-2 character code: strip, upper, zero-pad pure digits to 2 chars."""
    s = (x or "").strip().upper()
    return s.zfill(2) if s.isdigit() and len(s) < 2 else s


SUPPRESS_BELOW = 11
