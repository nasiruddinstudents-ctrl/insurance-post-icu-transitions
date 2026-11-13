"""
00_discover.py - list the Texas PUDF files the scripts can see and check their layout.

Run:   python 00_discover.py  "/path/to/your/Texas PUDF folder"

Output is safe to share: file names, column names and the hospital-level facility file
summary only. No patient records are printed.
"""
import sys
from collections import Counter

from tx_files import find_files, header_line, read_table
from tx_common import BASE1_KEEP, BASE2_KEEP, FACILITY_KEEP, FACILITY_COLUMNS

data_dir = sys.argv[1] if len(sys.argv) > 1 else "."
entries = find_files(data_dir)
print(f"Folder: {data_dir}\nFound {len(entries)} text files (including files inside zips)\n")

print(f"{'kind':<9} {'year':<5} {'q':<2}  file")
for e in sorted(entries, key=lambda e: (str(e['kind']), e['year'] or 0, e['qtr'] or 0)):
    name = e["path"] if e["member"] is None else f"{e['path']} :: {e['member']}"
    print(f"{str(e['kind']):<9} {str(e['year']):<5} {str(e['qtr']):<2}  {name}")

grid = Counter((e["kind"], e["year"], e["qtr"]) for e in entries)
print("\nQuarter grid (1 = file found):")
for kind in ["base1", "base2", "facility"]:
    row = []
    for y in range(2017, 2021):
        for q in range(1, 5):
            row.append(str(grid.get((kind, y, q), 0)))
    print(f"  {kind:<9} 2017Q1..2020Q4: {' '.join(row)}")

print("\nColumn check (first file of each kind):")
for kind, keep in [("base1", BASE1_KEEP), ("base2", BASE2_KEEP), ("facility", FACILITY_KEEP)]:
    e = next((x for x in entries if x["kind"] == kind), None)
    if e is None:
        print(f"  {kind}: NOT FOUND")
        continue
    cols = [c.strip().strip('"').upper() for c in header_line(e).split("\t")]
    has_header = "RECORD_ID" in cols or "THCIC_ID" in cols
    miss = [c for c in keep if c not in cols]
    print(f"  {kind}: {len(cols)} fields, header row: {has_header}; "
          f"needed columns missing: {miss if has_header else 'n/a (no header; positional layout)'}")

unknown = [e for e in entries if e["kind"] is None or e["year"] is None]
if unknown:
    print("\nFiles I could not classify (rename them to include base1/base2/facility and e.g. 1q2019):")
    for e in unknown:
        print("  ", e["path"], e["member"] or "")

fac = [e for e in entries if e["kind"] == "facility"]
if fac:
    df, _ = read_table(sorted(fac, key=lambda e: (e["year"] or 0, e["qtr"] or 0))[-1], FACILITY_KEEP,
                       default_columns=FACILITY_COLUMNS)
    print(f"\nFacility file (hospital-level, latest quarter): {len(df)} hospitals")
    for c in FACILITY_KEEP[1:]:
        print(f"  {c:<22} {dict(Counter(df[c]).most_common(6))}")
