"""Daily pipeline: scrape -> classify -> map -> score -> brief.json.
Raw post text and IDs-with-content exist ONLY inside this process; after this
script exits, the database holds issue-level aggregates and post IDs alone.

Usage:
  python scripts/run_pipeline.py            # manual run, prints the brief
  python scripts/run_pipeline.py --cron     # Hermes cron pre-run mode: last stdout
                                            # line is {"wakeAgent": ...} JSON
"""
import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import db                      # noqa: E402
import config_loader           # noqa: E402
import reddit_scraper          # noqa: E402
import facebook_scraper        # noqa: E402
import petitions_source        # noqa: E402
import planit_source           # noqa: E402
import fixmystreet_source      # noqa: E402
import council_news_source     # noqa: E402
import cmis_source             # noqa: E402
import classifier              # noqa: E402
import constituency_mapper     # noqa: E402
import scorer                  # noqa: E402

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / f"pipeline_{date.today()}.log"),
              logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("pipeline")


# Every source fails gracefully: one source down never blocks the brief.
# Temporarily disable sources with e.g. SOURCES_DISABLE=reddit,facebook
SOURCES = [
    ("reddit", reddit_scraper.scrape),
    ("facebook", facebook_scraper.scrape),
    ("petitions", petitions_source.scrape),
    ("planning", planit_source.scrape),
    ("fixmystreet", fixmystreet_source.scrape),
    ("council_news", council_news_source.scrape),
    ("cmis", cmis_source.scrape),
]


def apply_volume_caps(posts: list) -> list:
    """Cap what reaches the (free, rate-limited) classifier.

    config limits: max_items_per_source caps each source_type (highest
    engagement kept); max_classify_per_run is shared FAIRLY across source
    types round-robin, so one noisy source can't starve the others.
    Preclassified items (FMS trends) cost no tokens and always pass.
    Posts dropped here are NOT marked seen — they get another chance
    tomorrow while still recent. No limits configured (pilot) = no caps.
    """
    limits = config_loader.load_targets().get("limits") or {}
    cap = int(limits.get("max_classify_per_run") or 0)
    per_src = int(limits.get("max_items_per_source") or 0)
    if not cap and not per_src:
        return posts
    pre = [p for p in posts if p.get("preclassified")]
    by_src: dict[str, list] = {}
    for p in posts:
        if p.get("preclassified"):
            continue
        by_src.setdefault(p.get("source_type") or p.get("platform") or "?",
                          []).append(p)
    for src, lst in by_src.items():
        lst.sort(key=lambda p: (p.get("score") or 0) + (p.get("num_comments") or 0),
                 reverse=True)
        if per_src:
            by_src[src] = lst[:per_src]
    if cap:
        kept, queues = [], [q for q in by_src.values() if q]
        while queues and len(kept) < cap:
            for q in list(queues):
                if len(kept) >= cap:
                    break
                kept.append(q.pop(0))
                if not q:
                    queues.remove(q)
    else:
        kept = [p for lst in by_src.values() for p in lst]
    dropped = len(posts) - len(pre) - len(kept)
    if dropped:
        log.info("Volume caps: %d posts -> %d to classify (+%d preclassified); "
                 "%d deferred to tomorrow", len(posts), len(kept), len(pre), dropped)
    return pre + kept


def run() -> dict:
    conn = db.connect()
    errors = []
    posts = []
    disabled = {s.strip() for s in os.environ.get("SOURCES_DISABLE", "").split(",") if s.strip()}
    for name, fn in SOURCES:
        if name in disabled:
            log.info("Source %s disabled via SOURCES_DISABLE — skipping.", name)
            continue
        try:
            posts += fn(conn)
        except Exception as e:
            errors.append(f"{name}: {e}")
            log.exception("%s scrape failed", name)

    to_classify = apply_volume_caps(posts) if posts else []
    items = []
    if to_classify:
        try:
            items = classifier.classify(to_classify)
            items = constituency_mapper.map_items(conn, items)
        except Exception as e:
            errors.append(f"classify/map: {e}")
            log.exception("Classification failed")

    top = scorer.group_and_score(conn, items) if items else []
    # discard: only IDs are kept. Cap-deferred posts are NOT marked seen,
    # so they can come back tomorrow while still recent.
    reddit_scraper.mark_seen(conn, to_classify)
    conn.execute("INSERT INTO run_log (ran_at, source, posts_pulled, posts_kept, issues_written, errors) "
                 "VALUES (datetime('now'),?,?,?,?,?)",
                 ("+".join(n for n, _ in SOURCES if n not in disabled),
                  len(posts), len(items), len(top), "; ".join(errors)))
    conn.commit()
    conn.close()

    brief = {"date": str(date.today()), "items": top, "errors": errors,
             "posts_scanned": len(posts), "civic_items": len(items)}
    out = PROJECT_ROOT / "data" / f"brief_{date.today()}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(brief, indent=2))
    brief["path"] = str(out)
    return brief


def _constituency_regions() -> dict:
    """constituency name (lower) -> region/nation, from the reference CSV."""
    import csv
    ref = PROJECT_ROOT / "data" / "reference" / "uk_constituencies.csv"
    out = {}
    if ref.exists():
        for r in csv.DictReader(ref.open()):
            out[r["name"].strip().lower()] = (r.get("region")
                                              or r.get("nation") or "").strip()
    return out


def _item_region(i: dict, cons_regions: dict, cfg_regions: dict) -> str:
    c_raw = (i.get("constituency") or "").strip()
    if c_raw and cons_regions.get(c_raw.lower()):
        return cons_regions[c_raw.lower()]
    # Unresolved items look like "Argyll and Bute (constituency unresolved)" —
    # recover the council name and use the regions map.
    area = i.get("area") or ""
    if "(London)" in area or "(London)" in c_raw:
        return "London"
    return (cfg_regions.get(c_raw.split(" (")[0].strip())
            or cfg_regions.get(area.split(" (")[0].strip()) or "Other")


def print_human(brief: dict):
    """Region-grouped brief: only constituencies that HAVE items appear.
    Compact lines — Telegram-safe at national top_n volumes."""
    print(f"\n=== State Civic Brief — {brief['date']} ===")
    print(f"(scanned {brief['posts_scanned']} posts -> "
          f"{brief['civic_items']} civic items, top {len(brief['items'])} briefed)")
    if brief["errors"]:
        print("⚠ errors:", "; ".join(brief["errors"]))
    if not brief["items"]:
        print("\nNo new civic issues today.")
        return
    cons_regions = _constituency_regions()
    cfg_regions = config_loader.load_targets().get("regions") or {}
    by_region: dict[str, dict[str, list]] = {}
    for i in brief["items"]:
        region = _item_region(i, cons_regions, cfg_regions)
        cons = (i.get("constituency") or "").strip() or "Constituency unresolved"
        by_region.setdefault(region, {}).setdefault(cons, []).append(i)
    for region in sorted(by_region, key=lambda r: (r == "Other", r)):
        print(f"\n━━ {region} ━━")
        for cons, its in sorted(by_region[region].items()):
            mp = its[0].get("mp_name") or ""
            print(f"\n{cons}{' — MP: ' + mp if mp else ''}")
            for i in its:
                flag = " 🔥" if i["trending"] else ""
                src = i.get("source_type", i.get("source_platform", "?"))
                print(f"  • [{i['category']}|{src}] {i['summary']}{flag}")
                print(f"    vol {i['volume']} | eng {i['engagement']} | "
                      f"action: {i['suggested_action']} | {i['source_link']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cron", action="store_true", help="Hermes cron pre-run mode")
    args = ap.parse_args()
    b = run()
    if args.cron:
        # Wake the agent only if there is something to brief (or an error to report).
        wake = bool(b["items"]) or bool(b["errors"])
        print(json.dumps({"wakeAgent": wake,
                          "context": {"brief_path": b["path"],
                                      "item_count": len(b["items"]),
                                      "errors": b["errors"]}}))
    else:
        print_human(b)
