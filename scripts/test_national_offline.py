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


# --- cmis_source: calendar parser + post shape -------------------------------
import cmis_source as cs
from datetime import date as _date

_CAL = ('''<td><div class="rsDateBox"><a href="#2026-06-01" title="01/06/2026"
 class="rsDateHeader">1 Jun</a></div>
<div id="x_0_0" title="14:00 Valuation Joint Board : Room 1" class="rsApt">
</div></td>
<td><div class="rsDateBox"><a href="#2026-06-09" title="09/06/2026"
 class="rsDateHeader">9</a></div>
<div id="x_1_0" title="10:00 Planning Committee : Committee Room 1 &amp; 2"
 class="rsApt"></div>
<div id="x_2_0" title="Education Committee" class="rsApt"></div></td>''')
_ms = cs.parse_calendar(_CAL)
ok("cmis: parses dated appointments incl. untimed + html entities",
   len(_ms) == 3 and _ms[0]["time"] == "14:00"
   and _ms[1] == {"date": "2026-06-09", "time": "10:00",
                  "committee": "Planning Committee",
                  "location": "Committee Room 1 & 2"}
   and _ms[2]["time"] == "" and _ms[2]["committee"] == "Education Committee")
_site = {"name": "testville", "label": "Testville",
         "base": "https://testville.cmis.uk.com/testville",
         "pages": ["Committees.aspx", "Meetings.aspx"]}
_cfg = {"days_ahead": 8, "max_per_site": 6}
_items = cs._items_for(_site, _ms, _cfg, today=_date(2026, 6, 1))
ok("cmis: key-committee filter + window -> standard post shape",
   len(_items) == 1
   and _items[0]["source_type"] == "council_agenda"
   and _items[0]["city"] == "Testville"
   and _items[0]["id"].startswith("cmis:testville:2026-06-09:planning")
   and "Committee Room 1" in _items[0]["body"])
ok("cmis: out-of-window meetings dropped",
   cs._items_for(_site, _ms, _cfg, today=_date(2026, 6, 15)) == [])
ok("cmis: meetings url prefers the calendar page",
   cs.meetings_url(_site).endswith("/Meetings.aspx"))

# --- discover_cmis_feeds: worker + page detection ----------------------------
import discover_cmis_feeds as dc
_dc_saved = (dc.time.sleep, dc.get, dc.head_alive)
dc.time.sleep = lambda s: None

class _Resp:
    def __init__(self, url, text, status=200):
        self.url, self.text, self.status_code = url, text, status
        self.ok = status < 400

_CMIS_HTML = ('<html><form><input id="__VIEWSTATE"/>'
              '<a href="Committee.aspx?id=1">Planning Committee</a></form></html>')

def _dc_alive(url):
    if url == "https://testborough.cmis.uk.com/":
        return _Resp("https://testborough.cmis.uk.com/TestBorough/Default.aspx", "x")
    if url == "https://birmingham.cmis.uk.com/":
        return _Resp(url, "not found", 404)   # alive host, broken root
    return None

dc.head_alive = _dc_alive

def _dc_get(url, timeout=15):
    if url in ("https://testborough.cmis.uk.com/TestBorough/Committee.aspx",
               "https://testborough.cmis.uk.com/TestBorough/Meetings.aspx"):
        return _Resp(url, _CMIS_HTML)
    raise Exception("404")

dc.get = _dc_get
_n, _r = dc._probe_council({"nice-name": "Testborough",
                            "gov-uk-slug": "testborough"})
ok("cmis worker: host + path learned from root redirect",
   _r["host"] == "https://testborough.cmis.uk.com"
   and _r["path"] == "TestBorough")
ok("cmis worker: verified pages + sample captured",
   _r["pages"] == ["Committee.aspx", "Meetings.aspx"]
   and "__VIEWSTATE" in _r["_sample"])
def _dc_get_bham(url, timeout=15):
    if url == "https://birmingham.cmis.uk.com/birmingham/Committee.aspx":
        return _Resp(url, _CMIS_HTML)
    import requests as _rq
    raise _rq.exceptions.HTTPError(response=_Resp(url, "", 404))

dc.get = _dc_get_bham
_, _rb = dc._probe_council({"nice-name": "Birmingham",
                            "gov-uk-slug": "birmingham"})
ok("cmis worker: 404 root no longer kills a live host (Birmingham case)",
   _rb["host"] == "https://birmingham.cmis.uk.com"
   and _rb["path"] == "birmingham" and _rb["pages"] == ["Committee.aspx"])
dc.head_alive = lambda url: None
_, _r2 = dc._probe_council({"nice-name": "Nowhere", "gov-uk-slug": "nowhere"})
ok("cmis worker: no host -> empty result",
   _r2["host"] is None and not _r2["pages"])
dc.head_alive = _dc_alive
dc.get = lambda url, timeout=15: _Resp(url, "<html>parked domain</html>")
_, _r3 = dc._probe_council({"nice-name": "Testborough",
                            "gov-uk-slug": "testborough"})
ok("cmis worker: live host without CMIS pages is rejected",
   _r3["host"] is None and "no CMIS pages" in _r3.get("note", ""))
ok("cmis: looks_cmis accepts real markers, rejects plain html",
   dc.looks_cmis('<a href="x.aspx">meeting</a> cmis')
   and not dc.looks_cmis("<html>committee</html>"))
dc.time.sleep, dc.get, dc.head_alive = _dc_saved

# --- discover_council_feeds: parallel worker --------------------------------
import discover_council_feeds as d
_saved = (d.time.sleep, d._find_host, d.probe_template, d.get)
d.time.sleep = lambda s: None
d._find_host = lambda c, hosts: ("https://democracy.testville.gov.uk",
                                 [("1", "Planning Committee"), ("2", "Cabinet"),
                                  ("3", "Allotments Panel")])
d.probe_template = lambda base, comms=None: "Type=2&CId={cid}"
d.get = lambda url, timeout=30: '<rss version="2.0"><channel/></rss>'
_, _base, _res, _feeds = d._probe_council({"nice-name": "Testville"}, set(), set())
ok("discover worker: 2 key feeds verified (Allotments excluded)",
   _res["feeds"] == 2 and len(_feeds) == 2)
ok("discover worker: feed url uses learned template",
   _feeds[0]["url"] ==
   "https://democracy.testville.gov.uk/mgRss.aspx?Type=2&CId=1")
_, _, _r2, _f2 = d._probe_council({"nice-name": "Testville"}, set(),
                                  {_feeds[0]["url"]})
ok("discover worker: existing url deduped", _r2["feeds"] == 1 and len(_f2) == 1)
d._find_host = lambda c, hosts: ("https://democracy.testville.gov.uk", [])
_, _, _r3, _f3 = d._probe_council({"nice-name": "Testville"}, set(), set())
ok("discover worker: already-covered host short-circuits",
   _r3.get("note") == "already covered" and not _f3)
d._find_host = lambda c, hosts: ("", [])
_, _b4, _r4, _f4 = d._probe_council({"nice-name": "Nowhere"}, set(), set())
ok("discover worker: no host -> empty result",
   not _b4 and _r4["host"] is None and not _f4)
d.time.sleep, d._find_host, d.probe_template, d.get = _saved

print(f"\n{sum(PASS)}/{len(PASS)} checks passed")
sys.exit(0 if all(PASS) else 1)
