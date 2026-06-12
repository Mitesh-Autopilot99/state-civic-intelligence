"""Download the two national reference datasets (one-time, re-run to refresh):

  1. All current UK local authorities (mySociety uk_local_authority_names_and_codes)
     -> data/reference/uk_councils.csv
     columns we use: nice-name, gss-code, nation, region, local-authority-type-name,
                     gov-uk-slug, lower-or-unitary, current-authority, pop-2020
  2. All 650 Westminster constituencies, 2024 boundaries (mySociety 2025-constituencies)
     -> data/reference/uk_constituencies.csv
     columns we use: name, gss_code, nation, region

Both are open data published by mySociety. Each URL is tried with a versioned
fallback; whatever downloads is validated (row counts, required columns) before
being written — a short or column-less file is rejected loudly.

Run ONCE on the Mac (needs open internet):
    python scripts/fetch_national_data.py
Then: python scripts/build_national_targets.py
"""
import csv
import io
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REF_DIR = PROJECT_ROOT / "data" / "reference"
UA = {"User-Agent": "state-civic-listener/1.0 (open-data reference download; "
                    "contact thakermitesh89@gmail.com)"}
TIMEOUT = 60

COUNCILS_URLS = [
    "https://pages.mysociety.org/uk_local_authority_names_and_codes/data/"
    "uk_la_past_current/latest/uk_local_authorities_current.csv",
]
CONSTITUENCIES_URLS = [
    "https://pages.mysociety.org/2025-constituencies/data/"
    "parliament_con_2025/latest/parl_constituencies_2025.csv",
    "https://pages.mysociety.org/2025-constituencies/data/"
    "parliament_con_2025/0.1.0/parl_constituencies_2025.csv",
]

DATASETS = [
    # (label, urls, output filename, required columns, minimum rows)
    ("councils", COUNCILS_URLS, "uk_councils.csv",
     {"nice-name", "gss-code", "nation", "region", "local-authority-type-name",
      "gov-uk-slug", "lower-or-unitary", "current-authority"}, 200),
    ("constituencies", CONSTITUENCIES_URLS, "uk_constituencies.csv",
     {"name", "nation", "region"}, 650),
]


def fetch_one(label: str, urls: list[str], out_name: str,
              required: set, min_rows: int) -> bool:
    for url in urls:
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            print(f"  {label}: {url} failed ({e}) — trying next")
            continue
        text = r.text.lstrip('\ufeff')
        rows = list(csv.DictReader(io.StringIO(text)))
        cols = set(rows[0].keys()) if rows else set()
        missing = required - cols
        if missing:
            print(f"  {label}: {url} downloaded but missing columns {sorted(missing)} "
                  f"— trying next")
            continue
        if len(rows) < min_rows:
            print(f"  {label}: {url} only has {len(rows)} rows (expected >= {min_rows}) "
                  f"— trying next")
            continue
        out = REF_DIR / out_name
        out.write_text(text)
        print(f"  {label}: OK — {len(rows)} rows -> {out.relative_to(PROJECT_ROOT)}")
        return True
    print(f"  {label}: FAILED — no source verified. Nothing written.")
    return False


def main():
    REF_DIR.mkdir(parents=True, exist_ok=True)
    print("Downloading national reference data (mySociety open data):")
    ok = all([fetch_one(*d) for d in DATASETS])
    if not ok:
        sys.exit(1)

    # quick plain-English sanity summary
    councils = list(csv.DictReader((REF_DIR / "uk_councils.csv").open()))
    current = [c for c in councils if c.get("current-authority") == "True"]
    lpas = [c for c in current if c.get("lower-or-unitary") == "True"]
    cons = list(csv.DictReader((REF_DIR / "uk_constituencies.csv").open()))
    by_nation: dict[str, int] = {}
    for c in cons:
        by_nation[c["nation"]] = by_nation.get(c["nation"], 0) + 1
    print(f"\nCurrent authorities: {len(current)} "
          f"(of which lower-tier/unitary i.e. planning authorities: {len(lpas)})")
    print(f"Constituencies: {len(cons)} — by nation: "
          + ", ".join(f"{k} {v}" for k, v in sorted(by_nation.items())))
    if len(cons) != 650:
        print("WARNING: expected exactly 650 constituencies.")
    print("\nNext: python scripts/build_national_targets.py")


if __name__ == "__main__":
    main()
