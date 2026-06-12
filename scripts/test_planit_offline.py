"""Offline test for planit_source.py — fixtures follow the PlanIt data
dictionary exactly (verified live 2026-06-12). No network needed.
Run:  python scripts/test_planit_offline.py"""
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
os.environ["STATE_INTEL_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")

import db             # noqa: E402
import planit_source  # noqa: E402

RECORDS = {
    "Croydon": [
        # high comment count -> keep, ranked first
        {"name": "Croydon/24/01234/FUL", "uid": "24/01234/FUL", "area_name": "Croydon",
         "start_date": "2026-06-10", "address": "12 Brighton Road, South Croydon CR2 6EA",
         "description": "Demolition of existing dwelling and erection of a nine storey block of 42 flats",
         "postcode": "CR2 6EA", "app_state": "Undecided", "app_size": "Large", "app_type": "Full",
         "link": "/planapplic/Croydon/24/01234/FUL/", "url": "https://publicaccess.croydon.gov.uk/x",
         "n_comments": 87, "ward_name": "South Croydon"},
        # below min_comments AND small AND no keywords -> drop
        {"name": "Croydon/24/05678/HSE", "uid": "24/05678/HSE", "area_name": "Croydon",
         "start_date": "2026-06-10", "address": "3 Acacia Ave CR0 1AA",
         "description": "Single storey rear extension",
         "postcode": "CR0 1AA", "app_state": "Undecided", "app_size": "Small", "app_type": "Full",
         "link": "/planapplic/Croydon/24/05678/HSE/", "url": "https://publicaccess.croydon.gov.uk/y",
         "n_comments": 0, "ward_name": "Addiscombe"},
        # n_comments missing (council doesn't publish) but keyword cue -> keep (fallback path)
        {"name": "Croydon/24/09999/TEL", "uid": "24/09999/TEL", "area_name": "Croydon",
         "start_date": "2026-06-09", "address": "Land at Whitehorse Rd CR0 2JH",
         "description": "Installation of 20m 5G telecommunications mast and equipment cabinets",
         "postcode": "CR0 2JH", "app_state": "Undecided", "app_size": "Small", "app_type": "Telecoms",
         "link": "/planapplic/Croydon/24/09999/TEL/", "url": "https://publicaccess.croydon.gov.uk/z",
         "n_comments": None, "ward_name": "Selhurst"},
    ],
    "Hackney": [
        {"name": "Hackney/2026/1111", "uid": "2026/1111", "area_name": "Hackney",
         "start_date": "2026-06-11", "address": "Dalston Lane E8 3AH",
         "description": "Change of use from retail to 24-hour takeaway",
         "postcode": "E8 3AH", "app_state": "Undecided", "app_size": "Small", "app_type": "Full",
         "link": "/planapplic/Hackney/2026/1111/", "url": "https://hackney.gov.uk/p/1111",
         "n_comments": 14, "ward_name": "Dalston"},
    ],
    "Lewisham": [], "Waltham Forest": [], "Ealing": [],
}


class FakeResp:
    status_code = 200
    headers = {}
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def fake_get(url, params=None, timeout=None, **kw):
    sel = params.get("select", "")
    assert "applicant" not in sel and "agent" not in sel and "case_officer" not in sel, \
        "GDPR: personal fields must never be requested"
    return FakeResp({"records": RECORDS.get(params["auth"], []), "total": 0})


planit_source.requests.get = fake_get
planit_source.SLEEP_BETWEEN = 0

conn = db.connect()
items = planit_source.scrape(conn)

ids = [i["id"] for i in items]
checks = {
    "high-comment app kept": "planit:Croydon/24/01234/FUL" in ids,
    "boring small app dropped": "planit:Croydon/24/05678/HSE" not in ids,
    "no-count + keyword cue kept (fallback)": "planit:Croydon/24/09999/TEL" in ids,
    "second borough kept": "planit:Hackney/2026/1111" in ids,
    "ranked by comments within borough": ids[0] == "planit:Croydon/24/01234/FUL",
    "postcode attached for precise mapping": all(i["postcode"] for i in items),
    "source_type tagged": all(i["source_type"] == "planning" for i in items),
    "engagement = comment count": next(
        i for i in items if i["id"] == "planit:Croydon/24/01234/FUL")["num_comments"] == 87,
    "GDPR: no personal fields in output": all(
        not set(i) & {"applicant_name", "agent_name", "case_officer", "author"} for i in items),
}
for i in items:
    conn.execute("INSERT OR IGNORE INTO seen_posts VALUES (?, datetime('now'))", (i["id"],))
conn.commit()
checks["dedupe: second run yields 0 items"] = len(planit_source.scrape(conn)) == 0

print()
ok = True
for name, passed in checks.items():
    print(("PASS  " if passed else "FAIL  ") + name)
    ok &= passed
print(f"\nItems produced: {len(items)}")
for i in items:
    print(f"  [{i['city']}] {i['title'][:70]} — {i['num_comments']} comments")
sys.exit(0 if ok else 1)
