"""Offline test for petitions_source.py using REAL API responses captured on
2026-06-12 (petition 722903) plus one synthetic local petition that over-indexes.
No network needed. Run:  python scripts/test_petitions_offline.py
PASS criteria printed at the end."""
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
os.environ["STATE_INTEL_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")

import db                # noqa: E402
import petitions_source  # noqa: E402

# --- fixtures: real structure, real constituency rows from petition 722903 ---
REAL_ROWS = [  # captured live from /petitions/722903.json
    {"name": "Croydon East", "ons_code": "E14001186", "mp": "Natasha Irons MP", "signature_count": 715},
    {"name": "Croydon South", "ons_code": "E14001187", "mp": "Rt Hon Chris Philp MP", "signature_count": 714},
    {"name": "Lewisham North", "ons_code": "E14001332", "mp": "Vicky Foxcroft MP", "signature_count": 949},
    {"name": "Ealing Central and Acton", "ons_code": "E14001207", "mp": "Dr Rupa Huq MP", "signature_count": 976},
    {"name": "Walthamstow", "ons_code": "E14001563", "mp": "Ms Stella Creasy MP", "signature_count": 756},
    {"name": "Manchester Central", "ons_code": "E14001340", "mp": "Lucy Powell MP", "signature_count": 1200},
]
# synthetic LOCAL petition: 4,000 total => avg ~6.2/constituency; Croydon East
# has 800 (~130x) and Lewisham North 350 (~57x) -> must be flagged
LOCAL_ROWS = [
    {"name": "Croydon East", "ons_code": "E14001186", "mp": "Natasha Irons MP", "signature_count": 800},
    {"name": "Lewisham North", "ons_code": "E14001332", "mp": "Vicky Foxcroft MP", "signature_count": 350},
    {"name": "Croydon South", "ons_code": "E14001187", "mp": "Rt Hon Chris Philp MP", "signature_count": 10},
]

LIST_PAGE = {
    "links": {"next": None},
    "data": [
        {"id": 722903, "links": {"self": "https://petition.parliament.uk/petitions/722903.json"},
         "attributes": {"action": "Repeal the Online Safety Act", "signature_count": 550136}},
        {"id": 999001, "links": {"self": "https://petition.parliament.uk/petitions/999001.json"},
         "attributes": {"action": "Stop the closure of Croydon's libraries", "signature_count": 4000}},
        {"id": 999002, "links": {"self": "https://petition.parliament.uk/petitions/999002.json"},
         "attributes": {"action": "Tiny petition below floor", "signature_count": 80}},
    ],
}
DETAILS = {
    "722903": {"data": {"attributes": {
        "action": "Repeal the Online Safety Act",
        "background": "We want the Government to repeal the Online Safety act.",
        "signature_count": 550136, "signatures_by_constituency": REAL_ROWS}}},
    "999001": {"data": {"attributes": {
        "action": "Stop the closure of Croydon's libraries",
        "background": "Croydon Council plans to close five libraries.",
        "signature_count": 4000, "signatures_by_constituency": LOCAL_ROWS}}},
}


class FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def fake_get(url, timeout=None, **kw):
    if "petitions.json" in url:
        return FakeResp(LIST_PAGE)
    pid = url.rstrip(".json").rsplit("/", 1)[-1]
    return FakeResp(DETAILS[pid])


petitions_source.requests.get = fake_get
petitions_source.SLEEP_BETWEEN = 0

conn = db.connect()
items = petitions_source.scrape(conn)

checks = {
    "national petition (1x avg) NOT flagged": not any(i["id"].startswith("petition:722903") for i in items),
    "local petition flagged in Croydon East": any(
        i["constituency"] == "Croydon East" and "999001" in i["id"] for i in items),
    "local petition flagged in Lewisham North": any(
        i["constituency"] == "Lewisham North" and "999001" in i["id"] for i in items),
    "below-min-local (10 sigs) NOT flagged": not any(
        i["constituency"] == "Croydon South" for i in items),
    "MP name carried from API": all(i["mp_name"].endswith("MP") for i in items),
    "source_type tagged": all(i["source_type"] == "petition" for i in items),
    "GDPR: no personal fields": all(
        not set(i) & {"author", "user", "name", "email"} for i in items),
}
# dedupe: mark seen, scrape again -> 0 new items
for i in items:
    conn.execute("INSERT OR IGNORE INTO seen_posts VALUES (?, datetime('now'))", (i["id"],))
conn.commit()
checks["dedupe: second run yields 0 items"] = len(petitions_source.scrape(conn)) == 0
# frugality: petition_checks cache suppresses re-fetching unchanged petitions
row = conn.execute("SELECT COUNT(*) c FROM petition_checks").fetchone()
checks["petition_checks cache populated"] = row["c"] == 2

print()
ok = True
for name, passed in checks.items():
    print(("PASS  " if passed else "FAIL  ") + name)
    ok &= passed
print(f"\nItems produced: {len(items)}")
for i in items:
    print(f"  [{i['constituency']}] {i['title']} — {i['score']} local sigs — MP {i['mp_name']}")
sys.exit(0 if ok else 1)
