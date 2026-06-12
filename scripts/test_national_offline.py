"""Offline test for the national-scale layer (Phase 4). No network needed.
Covers: config_loader merge rules, build_national_targets generation +
idempotency, petitions national cap, planit labels + strike-out, pipeline
volume caps, region-grouped brief.
Run:  python scripts/test_national_offline.py
"""
import contextlib
import csv
import io
import os
import sys
import tempfile
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
os.environ["STATE_INTEL_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")

PASS = []


def ok(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    PASS.append(bool(cond))


# ---------------------------------------------------------------- config_loader
import config_loader  # noqa: E402

tmp = Path(tempfile.mkdtemp())
pilot_p, nat_p = tmp / "targets.yaml", tmp / "targets_national.yaml"
pilot_p.write_text(yaml.safe_dump({
    "target_constituencies": ["Croydon South"],
    "planit": {"boroughs": ["Croydon"], "max_per_borough": 8},
    "council_news": {"feeds": [{"name": "a", "url": "http://a/feed", "status": "verified"}]},
}))
nat_p.write_text(yaml.safe_dump({
    "target_constituencies": ["Croydon South", "Fife North East"],
    "planit": {"boroughs": ["Croydon", "Fife"], "max_per_borough": 3},
    "council_news": {"feeds": [{"name": "a-dup", "url": "http://a/feed"},
                               {"name": "b", "url": "http://b/feed"}]},
    "regions": {"Fife": "Scotland"},
    "limits": {"top_n": 40},
}))
orig_paths = (config_loader.PILOT, config_loader.NATIONAL)
config_loader.PILOT, config_loader.NATIONAL = pilot_p, nat_p
cfg = config_loader.load_targets()
ok("merge: lists concat deduped by url", len(cfg["council_news"]["feeds"]) == 2)
ok("merge: pilot scalar wins (max_per_borough 8)", cfg["planit"]["max_per_borough"] == 8)
ok("merge: national-only keys merge in (limits, regions)",
   cfg["limits"]["top_n"] == 40 and cfg["regions"]["Fife"] == "Scotland")
ok("merge: constituencies union", sorted(cfg["target_constituencies"]) ==
   ["Croydon South", "Fife North East"])
config_loader.NATIONAL = tmp / "missing.yaml"
ok("merge: no national file -> exactly pilot",
   config_loader.load_targets() == yaml.safe_load(pilot_p.read_text()))
config_loader.PILOT, config_loader.NATIONAL = orig_paths

# ------------------------------------------------------- build_national_targets
import build_national_targets as bnt  # noqa: E402

ref = tmp / "reference"
ref.mkdir()
with (ref / "uk_councils.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["nice-name", "gov-uk-slug", "local-authority-code",
                                      "region", "nation", "lower-or-unitary",
                                      "current-authority"])
    w.writeheader()
    w.writerow({"nice-name": "Croydon", "gov-uk-slug": "croydon", "local-authority-code": "CRY",
                "region": "London", "nation": "England", "lower-or-unitary": "True",
                "current-authority": "True"})
    w.writerow({"nice-name": "Fife", "gov-uk-slug": "fife", "local-authority-code": "FIF",
                "region": "", "nation": "Scotland", "lower-or-unitary": "True",
                "current-authority": "True"})
    w.writerow({"nice-name": "Kent", "gov-uk-slug": "kent", "local-authority-code": "KEN",
                "region": "South East", "nation": "England", "lower-or-unitary": "False",
                "current-authority": "True"})    # county: FMS yes, gnews/planit no
    w.writerow({"nice-name": "Old Defunct", "gov-uk-slug": "old", "local-authority-code": "OLD",
                "region": "", "nation": "England", "lower-or-unitary": "True",
                "current-authority": "False"})   # not current: excluded everywhere
with (ref / "uk_constituencies.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["name", "region", "nation"])
    w.writeheader()
    w.writerow({"name": "Croydon South", "region": "London", "nation": "England"})
    w.writerow({"name": "North East Fife", "region": "", "nation": "Scotland"})

bnt.REF_DIR, bnt.PILOT, bnt.NATIONAL = ref, pilot_p, tmp / "gen_national.yaml"
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bnt.main()
gen = yaml.safe_load(bnt.NATIONAL.read_text())
ok("bnt: all constituencies listed", len(gen["target_constituencies"]) == 2)
ok("bnt: FMS covers counties too (Kent), not defunct",
   {c["name"] for c in gen["fixmystreet"]["councils"]} == {"Croydon", "Fife", "Kent"})
ok("bnt: gnews only lower/unitary",
   {f["label"] for f in gen["council_news"]["feeds"]} == {"Croydon", "Fife"})
ok("bnt: planit only lower/unitary", gen["planit"]["boroughs"] == ["Croydon", "Fife"])
ok("bnt: regions fall back to nation", gen["regions"]["Fife"] == "Scotland"
   and gen["regions"]["Croydon"] == "London")
ok("bnt: national knobs present", gen["petitions"]["national"] is True
   and gen["limits"]["max_classify_per_run"] == 300)

# idempotency: flip one status + add a discovered feed, regenerate
gen["fixmystreet"]["councils"][0]["status"] = "verified"
gen["council_news"]["feeds"].append({"name": "icnn-x", "kind": "local_news",
                                     "label": "Fife", "url": "http://x/feed",
                                     "status": "verified"})
bnt.NATIONAL.write_text(yaml.safe_dump(gen, sort_keys=False))
with contextlib.redirect_stdout(buf):
    bnt.main()
gen2 = yaml.safe_load(bnt.NATIONAL.read_text())
ok("bnt: re-run preserves verified status",
   gen2["fixmystreet"]["councils"][0]["status"] == "verified")
ok("bnt: re-run keeps discovered/seeded feeds",
   any(f["name"] == "icnn-x" for f in gen2["council_news"]["feeds"]))

# --------------------------------------------------------- petitions national
import petitions_source as ps  # noqa: E402

ROWS = [{"name": f"Seat {i}", "ons_code": f"E{i:08d}", "mp": f"MP {i}",
         "signature_count": 100 + i * 50} for i in range(8)]
DETAIL = {"action": "Fix the trains", "background": "bg",
          "signature_count": 6500, "signatures_by_constituency": ROWS}
ps.load_config = lambda: {"enabled": True, "signature_floor": 500,
                          "over_index_ratio": 2.0, "min_local_signatures": 25,
                          "max_detail_fetches": 10, "national": True,
                          "max_per_petition": 5, "targets": []}
ps._list_open_petitions = lambda floor: [{"id": "1", "count": 6500, "action": "Fix the trains"}]
ps._detail = lambda pid: DETAIL
ps.SLEEP_BETWEEN = 0
import db  # noqa: E402
conn = db.connect()
items = ps.scrape(conn)
# avg = 10/constituency; rows 0..7 have 100..450 sigs -> all >=2x avg & >=25
ok("petitions national: capped at max_per_petition", len(items) == 5)
ok("petitions national: keeps MOST over-indexing seats",
   {i["constituency"] for i in items} == {f"Seat {i}" for i in range(3, 8)})
ok("petitions national: no targets filter applied",
   all(i["constituency"].startswith("Seat") for i in items))

# ------------------------------------------------------------- planit national
import planit_source as pl  # noqa: E402

ok("planit label: pilot mode keeps (London) suffix",
   pl._label("Croydon", {}) == "Croydon (London)")
ok("planit label: national non-London plain",
   pl._label("Fife", {"Fife": "Scotland"}) == "Fife")
ok("planit label: national London keeps suffix",
   pl._label("Hackney", {"Hackney": "London"}) == "Hackney (London)")
conn.executescript(pl.STRIKES_SCHEMA)
conn.execute("INSERT INTO planit_strikes VALUES ('Failville', 3, datetime('now'))")
conn.execute("INSERT INTO planit_strikes VALUES ('Okton', 1, datetime('now'))")
conn.execute("INSERT INTO planit_strikes VALUES ('Oldfail', 5, datetime('now','-8 days'))")
ok("planit strikes: 3+ recent fails -> skipped", pl._struck_out(conn, "Failville"))
ok("planit strikes: <3 fails -> still tried", not pl._struck_out(conn, "Okton"))
ok("planit strikes: old fails -> weekly retry", not pl._struck_out(conn, "Oldfail"))

# rotation cursor + abort-on-429
orig_pl_load, orig_fetch = pl.load_config, pl._fetch_borough
pl.load_config = lambda: {"enabled": True, "boroughs": list("ABCDE"),
                          "recent_days": 3, "pg_sz": 100, "min_comments": 3,
                          "max_per_borough": 8, "max_boroughs_per_run": 2,
                          "regions": {}}
pl.SLEEP_BETWEEN = 0
calls = []
pl._fetch_borough = lambda b, cfg, recent: (calls.append((b, recent)), [])[1]
pl.scrape(conn)
ok("planit rotation: first slice + widened lookback",
   [c[0] for c in calls] == ["A", "B"] and calls[0][1] == 4)
calls.clear(); pl.scrape(conn)
ok("planit rotation: cursor advances", [c[0] for c in calls] == ["C", "D"])
calls.clear(); pl.scrape(conn)
ok("planit rotation: wraps around", [c[0] for c in calls] == ["E", "A"])


def _flaky(b, cfg, recent):
    calls.append((b, recent))
    if b == "B":
        raise pl.RateLimited(b)
    return []


pl._fetch_borough = _flaky
calls.clear(); pl.scrape(conn)
struck_b = conn.execute(
    "SELECT COUNT(*) c FROM planit_strikes WHERE area='B'").fetchone()["c"]
ok("planit 429: pass aborts, no strike recorded",
   [c[0] for c in calls] == ["B"] and struck_b == 0)
pl._fetch_borough = lambda b, cfg, recent: (calls.append((b, recent)), [])[1]
calls.clear(); pl.scrape(conn)
ok("planit 429: next run resumes at same borough",
   [c[0] for c in calls] == ["B", "C"])
ok("planit auth variants: Aberdeen City -> Aberdeen",
   pl._auth_variants("Aberdeen City")[:2] == ["Aberdeen City", "Aberdeen"])
pl.load_config, pl._fetch_borough = orig_pl_load, orig_fetch

# ------------------------------------------------- volume caps + brief grouping
import run_pipeline as rp  # noqa: E402
import config_loader as cl  # noqa: E402

orig_load = cl.load_targets
cl.load_targets = lambda: {"limits": {"max_classify_per_run": 4,
                                      "max_items_per_source": 3}}
posts = ([{"source_type": "google_news", "score": s, "num_comments": 0}
          for s in range(9, 0, -1)] +
         [{"source_type": "petition", "score": 100, "num_comments": 0}] +
         [{"source_type": "fixmystreet", "preclassified": True,
           "score": 0, "num_comments": 0}])
capped = rp.apply_volume_caps(posts)
rest = [p for p in capped if not p.get("preclassified")]
ok("caps: preclassified always passes",
   sum(1 for p in capped if p.get("preclassified")) == 1)
ok("caps: global cap enforced", len(rest) == 4)
ok("caps: fair share — minority source gets its slot",
   sum(1 for p in rest if p["source_type"] == "petition") == 1)
ok("caps: highest engagement survives",
   max(p["score"] for p in rest if p["source_type"] == "google_news") == 9)
cl.load_targets = lambda: {}
ok("caps: no limits (pilot) -> passthrough", rp.apply_volume_caps(posts) == posts)

cons_regions = {"croydon south": "London", "north east fife": "Scotland"}
rp._constituency_regions = lambda: cons_regions
cl.load_targets = lambda: {"regions": {"Fife": "Scotland"}}
mk = lambda **kw: {"category": "transport", "source_type": "petition", "summary": "s",
                   "trending": 0, "volume": 1, "engagement": 1, "mp_name": "",
                   "suggested_action": "watch", "source_link": "http://x",
                   "constituency": "", "area": "", **kw}
brief = {"date": "d", "posts_scanned": 1, "civic_items": 1, "errors": [],
         "items": [mk(constituency="Croydon South"),
                   mk(constituency="North East Fife"),
                   mk(area="Fife"),
                   mk(area="Nowhere")]}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rp.print_human(brief)
text = buf.getvalue()
ok("brief: grouped by region, constituency lookup",
   "━━ London ━━" in text and "━━ Scotland ━━" in text)
ok("brief: label fallback via regions map", text.count("━━ Scotland ━━") == 1
   and "Fife" in text)
ok("brief: unknown -> Other, printed last",
   "━━ Other ━━" in text and text.rindex("━━ Other") > text.rindex("━━ Scotland"))
ok("brief: only constituencies with items appear",
   "Croydon South" in text and "Seat 1" not in text)
ok("region: unresolved constituency recovers council via regions map",
   rp._item_region({"constituency": "Fife (constituency unresolved)"},
                   {}, {"Fife": "Scotland"}) == "Scotland")
ok("region: unresolved London council label",
   rp._item_region({"constituency": "Ealing (London) (constituency unresolved)"},
                   {}, {}) == "London")
cl.load_targets = orig_load
conn.close()

print(f"\n{sum(PASS)}/{len(PASS)} checks passed")
sys.exit(0 if all(PASS) else 1)
