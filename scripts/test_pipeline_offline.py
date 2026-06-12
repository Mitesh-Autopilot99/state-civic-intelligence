"""Offline END-TO-END pipeline test — the acceptance test's wiring, simulated.
All six sources + the LLM call are mocked with realistic fixtures; mapper
lookups are pre-seeded into the cache tables. No network needed.
Run:  python scripts/test_pipeline_offline.py

Phase 1: SOURCES_DISABLE=reddit,facebook -> brief must contain 10+ items from
         the four new sources across 3+ target constituencies, each with area,
         summary, source link and suggested action.
Phase 2: everything enabled -> combined brief with the source mix visible.
Phase 3: one source raising -> error reported, brief still generates.

(The LIVE acceptance run happens on the Mac — see README §3c / §5.)
"""
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
os.environ["STATE_INTEL_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
logging.basicConfig(level=logging.WARNING)   # pre-empt run_pipeline's file handler

import db                    # noqa: E402
import run_pipeline as rp    # noqa: E402
import classifier            # noqa: E402

rp.PROJECT_ROOT = Path(tempfile.mkdtemp())   # brief JSON goes to temp, not data/


# ---------- fixtures (shapes match each real source module) ----------
def _post(pid, src, city, title, **kw):
    base = {"id": pid, "subreddit": src, "city": city, "title": title, "body": "",
            "score": 0, "num_comments": 0, "permalink": f"https://example.org/{pid}",
            "created_utc": time.time(), "platform": src, "source_type": src}
    base.update(kw)
    return base


PETITIONS = [
    _post("petition:1:E1", "petition", "UK", "Save Croydon libraries",
          constituency="Croydon East", mp_name="MP One", score=480,
          body="[480 signatures in Croydon East — 11.0x the national average]"),
    _post("petition:2:E2", "petition", "UK", "Stop Lewisham incinerator expansion",
          constituency="Lewisham East", mp_name="MP Two", score=350),
    _post("petition:3:E3", "petition", "UK", "Fund Hackney youth services properly",
          constituency="Hackney South and Shoreditch", mp_name="MP Three", score=290),
    _post("petition:4:E4", "petition", "UK", "Reopen Walthamstow ticket offices",
          constituency="Walthamstow", mp_name="MP Four", score=260),
]
PLANNING = [
    _post("planit:Croydon/1", "planning", "Croydon", "Demolition of cinema for 29-storey tower",
          postcode="CR0 1AB", num_comments=44),
    _post("planit:Lewisham/2", "planning", "Lewisham", "HMO conversion, 12 Catford Hill",
          postcode="SE6 4AA", num_comments=9),
    _post("planit:Ealing/3", "planning", "Ealing", "5G mast outside primary school",
          postcode="W5 2BB", num_comments=17),
]
FMS = [_post("fms-trend:Croydon:potholes_roads:2026w24", "fixmystreet", "Croydon (London)",
             "potholes/roads reports rising in Croydon", score=26,
             preclassified=True, category="potholes_roads", urgency=3, specificity=3,
             area="Croydon",
             summary="potholes/roads reports in Croydon up 3.2x: 26 in the last 7 days vs ~8 expected")]
NEWS = [
    _post("news:https://x/1", "local_news", "Croydon (London)",
          "Council approves controversial Purley Way housing scheme"),
    _post("news:https://x/2", "council_agenda", "Lewisham (London)",
          "Planning Committee A - Agenda published, 18/06/2026"),
    _post("news:https://x/3", "local_news", "Hackney (London)",
          "Fly-tipping complaints surge near Hackney Downs"),
]
REDDIT = [_post("t3_aaa", "reddit", "Croydon (London)", "Bins missed three weeks running in Addiscombe",
                score=120, num_comments=38)]
FACEBOOK = [_post("fb_bbb", "facebook", "Lewisham (London)", "Anyone else's street lights out on Catford Hill?",
                  score=55, num_comments=21)]

# rp.SOURCES bound the real functions at import time — replace the list itself
rp.SOURCES = [
    ("reddit", lambda conn: list(REDDIT)),
    ("facebook", lambda conn: list(FACEBOOK)),
    ("petitions", lambda conn: list(PETITIONS)),
    ("planning", lambda conn: list(PLANNING)),
    ("fixmystreet", lambda conn: list(FMS)),
    ("council_news", lambda conn: list(NEWS)),
]

# ---------- mock the LLM: classify every numbered post as civic ----------
def fake_call(model, content):
    import json, re
    out = []
    for line in content.splitlines():
        m = re.match(r"^(\d+)\. \[r/[^,]+, [^\]]+\] (.+?)(?: — |$)", line)
        if m:
            out.append({"n": int(m.group(1)), "is_civic": True, "category": "housing",
                        "urgency": 3, "specificity": 4, "area": "",
                        "summary": m.group(2)[:120]})
    return json.dumps(out)


classifier._call = fake_call
classifier.time.sleep = lambda s: None

# ---------- pre-seed mapper caches (postcodes.io / Members API offline) ----------
conn = db.connect()
for pc, c in [("PC:CR0 1AB", "Croydon West"), ("PC:SE6 4AA", "Lewisham East"),
              ("PC:W5 2BB", "Ealing Central and Acton")]:
    conn.execute("INSERT OR REPLACE INTO area_cache VALUES (?,?,datetime('now'))",
                 (pc.replace("PC:", "pc:"), c))
conn.execute("INSERT OR REPLACE INTO area_cache VALUES (?,?,datetime('now'))",
             ("croydon|croydon (london)", "Croydon West"))
for c in ["Croydon West", "Lewisham East", "Ealing Central and Acton"]:
    conn.execute("INSERT OR REPLACE INTO mp_cache VALUES (?,?,?,datetime('now'))",
                 (c, f"MP for {c}", "Test Party"))
conn.commit()
conn.close()

TARGETS = {"Croydon East", "Croydon West", "Lewisham East", "Ealing Central and Acton",
           "Hackney South and Shoreditch", "Walthamstow"}
NEW_TYPES = {"petition", "planning", "fixmystreet", "council_agenda", "local_news"}

# ---------- Phase 1: social disabled ----------
os.environ["SOURCES_DISABLE"] = "reddit,facebook"
b1 = rp.run()
new_items = [i for i in b1["items"] if i["source_type"] in NEW_TYPES]
consts = {i["constituency"] for i in new_items if i["constituency"] in TARGETS}
checks = {
    "P1: no errors": not b1["errors"],
    "P1: 10+ items from new sources": len(new_items) >= 10,
    "P1: zero social items": all(i["source_type"] in NEW_TYPES for i in b1["items"]),
    "P1: 3+ target constituencies": len(consts) >= 3,
    "P1: every item has area": all(i["area"] for i in b1["items"]),
    "P1: every item has summary": all(i["summary"] for i in b1["items"]),
    "P1: every item has source link": all(i["source_link"].startswith("https://") for i in b1["items"]),
    "P1: every item has suggested action": all(i["suggested_action"] in
                                               ("seed_motion", "outreach", "watch") for i in b1["items"]),
    "P1: petition outranks news (source bonus)": (
        [i["source_type"] for i in b1["items"]].index("petition")
        < [i["source_type"] for i in b1["items"]].index("local_news")),
}

# ---------- Phase 2: everything enabled ----------
os.environ["SOURCES_DISABLE"] = ""
b2 = rp.run()
mix = {i["source_type"] for i in b2["items"]}
checks.update({
    "P2: no errors": not b2["errors"],
    "P2: social back in the mix": {"reddit", "facebook"} <= mix,
    "P2: 5+ source types visible in one brief": len(mix) >= 5,
    "P2: source_type on every item": all(i.get("source_type") for i in b2["items"]),
})

# ---------- Phase 3: one source down -> graceful ----------
def boom(conn):
    raise RuntimeError("simulated PlanIt outage")
rp.SOURCES = [(n, boom if n == "planning" else f) for n, f in rp.SOURCES]
b3 = rp.run()
checks.update({
    "P3: error reported, not fatal": any("planning" in e for e in b3["errors"]),
    "P3: brief still generates": len(b3["items"]) > 0,
})

print()
ok = True
for name, passed in checks.items():
    print(("PASS  " if passed else "FAIL  ") + name)
    ok &= passed
print(f"\nPhase 1 ({len(b1['items'])} items, social disabled):")
for i in b1["items"]:
    print(f"  [{i['source_type']:>14}] {i['constituency']:<32} {i['summary'][:60]}  -> {i['suggested_action']}")
print(f"\nPhase 2 source mix: {sorted(mix)}")
sys.exit(0 if ok else 1)
