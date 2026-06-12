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
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import db                      # noqa: E402
import reddit_scraper          # noqa: E402
import facebook_scraper        # noqa: E402
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


def run() -> dict:
    conn = db.connect()
    errors = []
    posts = []
    try:
        posts = reddit_scraper.scrape(conn)
    except Exception as e:
        errors.append(f"reddit: {e}")
        log.exception("Reddit scrape failed")
    try:
        posts += facebook_scraper.scrape(conn)   # never blocks the brief
    except Exception as e:
        errors.append(f"facebook: {e}")
        log.exception("Facebook scrape failed")

    items = []
    if posts:
        try:
            items = classifier.classify(posts)
            items = constituency_mapper.map_items(conn, items)
        except Exception as e:
            errors.append(f"classify/map: {e}")
            log.exception("Classification failed")

    top = scorer.group_and_score(conn, items) if items else []
    reddit_scraper.mark_seen(conn, posts)  # discard: only IDs are kept
    conn.execute("INSERT INTO run_log (ran_at, source, posts_pulled, posts_kept, issues_written, errors) "
                 "VALUES (datetime('now'),'reddit+facebook',?,?,?,?)",
                 (len(posts), len(items), len(top), "; ".join(errors)))
    conn.commit()
    conn.close()

    brief = {"date": str(date.today()), "items": top, "errors": errors,
             "posts_scanned": len(posts), "civic_items": len(items)}
    out = PROJECT_ROOT / "data" / f"brief_{date.today()}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(brief, indent=2))
    brief["path"] = str(out)
    return brief


def print_human(brief: dict):
    print(f"\n=== State Civic Brief — {brief['date']} ===")
    print(f"(scanned {brief['posts_scanned']} posts -> {brief['civic_items']} civic items)\n")
    if brief["errors"]:
        print("⚠ errors:", "; ".join(brief["errors"]), "\n")
    for n, i in enumerate(brief["items"], 1):
        flag = " 🔥TRENDING" if i["trending"] else ""
        print(f"{n}. [{i['category']}] {i['area']} — {i['summary']}{flag}")
        print(f"   {i['constituency']} | MP: {i['mp_name'] or 'n/a'} | "
              f"vol {i['volume']} | eng {i['engagement']} | action: {i['suggested_action']}")
        print(f"   {i['source_link']}\n")
    if not brief["items"]:
        print("No new civic issues today.")


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
