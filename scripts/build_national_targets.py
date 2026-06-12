"""Generate config/targets_national.yaml from the reference data downloaded by
fetch_national_data.py. Covers every current UK local authority + all 650
constituencies. The pilot file config/targets.yaml is NEVER touched; at runtime
config_loader merges the two (pilot wins conflicts, duplicates removed).

What gets generated:
  fixmystreet.councils   one RSS feed per current authority (counties included —
                         highways reports go to them), status: candidate
  council_news.feeds     one Google News query feed per lower-tier/unitary
                         authority, status: candidate
  planit.boroughs        every lower-tier/unitary authority (the planning authorities)
  target_constituencies  all 650
  regions                label -> region map for brief grouping
  petitions/limits       national-mode knobs (only keys the pilot doesn't set)

Statuses are NOT decided here: everything starts 'candidate' and
scripts/verify_feeds.py flips each to verified/dead by probing. Re-running this
generator preserves statuses already set, and preserves any feeds added by
discover_council_feeds.py / seed_local_news.py.

Run on the Mac after fetch_national_data.py:
    python scripts/build_national_targets.py
Then:
    python scripts/verify_feeds.py          (probes candidates; ~25 min, polite 1/s)
    python scripts/discover_council_feeds.py --national   (ModernGov agendas)
"""
import csv
import sys
from pathlib import Path
from urllib.parse import quote

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REF_DIR = PROJECT_ROOT / "data" / "reference"
PILOT = PROJECT_ROOT / "config" / "targets.yaml"
NATIONAL = PROJECT_ROOT / "config" / "targets_national.yaml"

GNEWS_TEMPLATE = ("https://news.google.com/rss/search?q=%22{name}%22+"
                  "(council+OR+planning+OR+housing+OR+bins+OR+crime+OR+NHS+OR+roadworks)"
                  "+when:2d&hl=en-GB&gl=GB&ceid=GB:en")


def load_reference():
    councils_path = REF_DIR / "uk_councils.csv"
    cons_path = REF_DIR / "uk_constituencies.csv"
    if not councils_path.exists() or not cons_path.exists():
        sys.exit("Reference data missing — run scripts/fetch_national_data.py first.")
    councils = [c for c in csv.DictReader(councils_path.open())
                if c.get("current-authority") == "True"]
    cons = list(csv.DictReader(cons_path.open()))
    return councils, cons


def main():
    councils, cons = load_reference()
    lpas = [c for c in councils if c.get("lower-or-unitary") == "True"]

    pilot = yaml.safe_load(PILOT.read_text()) or {}
    pilot_fms_urls = {c.get("feed") for c in (pilot.get("fixmystreet") or {}).get("councils", [])}
    pilot_news_urls = {f.get("url") for f in (pilot.get("council_news") or {}).get("feeds", [])}

    # idempotency: keep statuses + discovered/seeded feeds from a previous run
    prev = yaml.safe_load(NATIONAL.read_text()) if NATIONAL.exists() else {}
    prev = prev or {}
    prev_status = {}
    for c in (prev.get("fixmystreet") or {}).get("councils", []):
        prev_status[c.get("feed")] = c.get("status")
    keep_feeds, prev_gnews_status = [], {}
    for f in (prev.get("council_news") or {}).get("feeds", []):
        if f.get("kind") == "google_news":
            prev_gnews_status[f.get("url")] = f.get("status")
        else:   # council_agenda / local_news written by discovery & seeder
            keep_feeds.append(f)

    fms = []
    for c in councils:
        name = c["nice-name"].strip()
        feed = f"https://www.fixmystreet.com/rss/reports/{quote(name.replace(' ', '+'), safe='+')}"
        if feed in pilot_fms_urls:
            continue
        fms.append({"name": name, "label": name, "feed": feed,
                    "status": prev_status.get(feed) or "candidate"})

    gnews = []
    for c in lpas:
        name = c["nice-name"].strip()
        url = GNEWS_TEMPLATE.format(name=quote(name))
        if url in pilot_news_urls:
            continue
        gnews.append({"name": f"gnews-{c['gov-uk-slug'] or c['local-authority-code'].lower()}",
                      "kind": "google_news", "label": name, "url": url,
                      "status": prev_gnews_status.get(url) or "candidate"})

    out = {
        "target_constituencies": sorted({r["name"].strip() for r in cons}),
        "regions": {c["nice-name"].strip(): (c.get("region") or c.get("nation") or "UK").strip()
                    for c in councils},
        "fixmystreet": {"councils": fms},
        "council_news": {"feeds": keep_feeds + gnews},
        "planit": {"boroughs": sorted({c["nice-name"].strip() for c in lpas})},
        # national-mode knobs — merged in only because the pilot file doesn't set them
        "petitions": {"national": True, "max_constituencies_per_petition": 5},
        "limits": {"max_classify_per_run": 300, "top_n": 40,
                   "max_items_per_source": 120},
    }
    NATIONAL.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True,
                                       width=1000))
    print(f"config/targets_national.yaml written:")
    print(f"  target_constituencies : {len(out['target_constituencies'])}")
    print(f"  fixmystreet councils  : {len(fms)} "
          f"({sum(1 for c in fms if c['status'] == 'candidate')} awaiting verify)")
    print(f"  google_news feeds     : {len(gnews)} "
          f"({sum(1 for f in gnews if f['status'] == 'candidate')} awaiting verify)")
    print(f"  kept discovered/seeded feeds : {len(keep_feeds)}")
    print(f"  planit authorities    : {len(out['planit']['boroughs'])}")
    print("\nNext: python scripts/verify_feeds.py")


if __name__ == "__main__":
    main()
