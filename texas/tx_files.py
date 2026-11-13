"""
tx_files.py - find and read the Texas PUDF tab-delimited files (plain or still zipped).
"""
import io
import os
import re
import zipfile

import pandas as pd

from tx_common import BASE1_COLUMNS

TEXT_EXT = (".txt", ".tab", ".tsv", ".csv", ".dat")
RE_KIND = [
    ("base2", re.compile(r"base[\s_\-#]*(data)?[\s_\-#]*2(?!\d)")),
    ("base1", re.compile(r"base[\s_\-#]*(data)?[\s_\-#]*1(?!\d)")),
    ("facility", re.compile(r"facilit|fac[\s_\-]*type")),
    ("charges", re.compile(r"charge")),
]
RE_QTR = [
    re.compile(r"(?<!\d)([1-4])\s*q\s*_?(20\d\d)"),      # 1q2019, 1Q_2019
    re.compile(r"q\s*([1-4])[\s_\-]*(20\d\d)"),           # q1_2019
    re.compile(r"(20\d\d)[\s_\-]*q\s*([1-4])"),           # 2019q1, 2019_Q1
]


def classify(name):
    low = os.path.basename(name).lower()
    kind = next((k for k, rx in RE_KIND if rx.search(low)), None)
    year = qtr = None
    for i, rx in enumerate(RE_QTR):
        m = rx.search(low)
        if m:
            a, b = m.groups()
            year, qtr = (int(b), int(a)) if i < 2 else (int(a), int(b))
            break
    return kind, year, qtr


def find_files(data_dir):
    """Return a list of dicts: kind, year, qtr, path, member (member is None for plain files)."""
    out = []
    for root, _, files in os.walk(data_dir):
        for f in sorted(files):
            p = os.path.join(root, f)
            low = f.lower()
            if low.endswith(".zip"):
                try:
                    with zipfile.ZipFile(p) as z:
                        for m in z.namelist():
                            if m.lower().endswith(TEXT_EXT) and not m.endswith("/"):
                                kind, y, q = classify(m)
                                if kind is None or y is None:   # fall back on the zip's own name
                                    k2, y2, q2 = classify(f)
                                    kind, y, q = kind or k2, y or y2, q or q2
                                out.append(dict(kind=kind, year=y, qtr=q, path=p, member=m))
                except zipfile.BadZipFile:
                    out.append(dict(kind="BAD_ZIP", year=None, qtr=None, path=p, member=None))
            elif low.endswith(TEXT_EXT):
                kind, y, q = classify(f)
                out.append(dict(kind=kind, year=y, qtr=q, path=p, member=None))
    return out


def _open_text(entry):
    if entry["member"] is None:
        return open(entry["path"], "r", encoding="latin-1", newline="")
    z = zipfile.ZipFile(entry["path"])
    return io.TextIOWrapper(z.open(entry["member"]), encoding="latin-1", newline="")


def header_line(entry):
    with _open_text(entry) as fh:
        return fh.readline().rstrip("\r\n")


def read_table(entry, keep, chunksize=None, default_columns=None):
    """Read only the `keep` columns (names normalised to upper case). Returns a DataFrame,
    or an iterator of DataFrames when chunksize is given. Columns missing from the file
    are added as empty strings so later code always sees the same layout."""
    first = header_line(entry)
    fields = [c.strip().strip('"').upper() for c in first.split("\t")]
    has_header = "RECORD_ID" in fields or "THCIC_ID" in fields
    if has_header:
        names = fields
        header = 0
    else:
        names = list(default_columns or BASE1_COLUMNS)
        if len(names) != len(fields):
            raise ValueError(f"{entry['path']}::{entry['member']} has no header and "
                             f"{len(fields)} fields; expected {len(names)}.")
        header = None
    present = sorted([c for c in keep if c in names], key=names.index)   # file order
    missing = [c for c in keep if c not in names]
    idx = [names.index(c) for c in present]

    def fix(df):
        df.columns = present
        for c in missing:
            df[c] = ""
        df = df[keep]
        for c in keep:
            df[c] = df[c].fillna("").astype(str).str.strip().str.strip('"')
        return df

    fh = _open_text(entry)
    reader = pd.read_csv(fh, sep="\t", header=header, usecols=idx, dtype=str, quoting=3,
                         keep_default_na=False, na_filter=False, chunksize=chunksize,
                         on_bad_lines="warn", engine="c")
    if chunksize is None:
        df = fix(reader)
        fh.close()
        return df, missing

    def gen():
        try:
            for ch in reader:
                yield fix(ch)
        finally:
            fh.close()
    return gen(), missing
