"""PlanIt source (planit.org.uk — free national aggregator of planning registers).

Pulls recent planning applications for the five pilot boroughs, prioritising
ones with public comments (n_comments) — the research adjustment: PlanIt exposes
a comment COUNT, not a separate objection count, and it's optional. Where it is
missing we fall back to app_size (Large/Medium) and contention keywords.

GDPR: the `select` clause requests ONLY the fields below. Applicant/agent/case
officer fields are never requested (PlanIt doesn't store the names anyway).

Fair use: one request per borough per day, pg_sz<=100 — far inside the
documented limits (5000 results / 1MB / 429+Retry-After on excess).

Output: same post-dict shape as the other scrapers, source_type='planning',
with `postcode` attached so the mapper can resolve the constituency precisely.

Standalone test:  python scripts/planit_source.py
"""
import logging
import sys
import time
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import db  # noqa: E402

log = logging.getLogger("planit")

API = "https://www.planit.org.uk/api/applics/json"
HTTP_TIMEOUT = 60
SLEEP_BETWEEN = 2          # seconds between borough requests
# ONLY these fields are requested; '->' lifts two safe keys out of other_fields
SELECT = ("name,uid,area_name,start_date,address,description,postcode,"
          "app_state,app_size,app_type,link,url,"
          "n_comments:other_fields->n_comments,ward_name:other_fields->ward_name")
# contention cues used only when a council publishes no comment counts
KEYWORDS = ["demolition", "demolish", "tower", "storey", "flats", "hmo",
            "telecommunications", "mast", "5g", "takeaway", "betting",
            "late night", "licence", "car park", "loss of", "change of use"]


def load_config() -> dict:
    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "targets.yaml").read_text())
    p = cfg.get("planit") or {}
    return {
        "enabled": p.get("enabled", True),
        "boroughs": p.get("boroughs", ["Croydon", "Lewisham", "Hackney",
                                       "Waltham Forest", "Ealing"]),
        "recent_days": int(p.get("recent_days", 3)),
        "pg_sz": int(p.get("pg_sz", 100)),
        "min_comments": int(p.get("min_comments", 3)),
        "max_per_borough": int(p.get("max_per_borough", 8)),
    }


def _fetch_borough(borough: str, cfg: dict) -> list[dict]:
    r = requests.get(API, params={
        "auth": borough,
        "recent": cfg["recent_days"],
        "pg_sz": cfg["pg_sz"],
        "select": SELECT,
        "sort": "start_date.desc.nullslast",
        "compress": "on",
    }, timeout=HTTP_TIMEOUT)
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 60))
        log.warning("PlanIt 429 for %s — waiting %ss once", borough, wait)
        time.sleep(min(wait, 120))
        r = requests.get(API, params={"auth": borough, "recent": cfg["recent_days"],
                                      "pg_sz": cfg["pg_sz"], "select": SELECT,
                                      "sort": "start_date.desc.nullslast",
                                      "compress": "on"}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json().get("records", [])


def _interesting(rec: dict, cfg: dict) -> bool:
    n = rec.get("n_comments")
    if n is not None:
        return int(n) >= cfg["min_comments"]
    if (rec.get("app_size") or "") in ("Large", "Medium"):
        return True
    desc = (rec.get("description") or "").lower()
    return any(k in desc for k in KEYWORDS)


def scrape(conn) -> list:
    cfg = load_config()
    if not cfg["enabled"]:
        log.info("PlanIt source disabled in config — skipping.")
        return []
    seen = {row["post_id"] for row in conn.execute("SELECT post_id FROM seen_posts")}
    out = []
    for borough in cfg["boroughs"]:
        try:
            records = _fetch_borough(borough, cfg)
        except Exception as e:
            log.error("PlanIt fetch failed for %s: %s", borough, e)
            continue
        time.sleep(SLEEP_BETWEEN)
        kept = []
        for rec in records:
            post_id = f"planit:{rec.get('name') or rec.get('uid')}"
            if post_id in seen or not rec.get("description"):
                continue
            if not _interesting(rec, cfg):
                continue
            n = int(rec.get("n_comments") or 0)
            ward = (rec.get("ward_name") or "").strip()
            desc = rec["description"].strip()
            addr = (rec.get("address") or "").strip()
            kept.append({
                "id": post_id,
                "subreddit": f"planit:{borough}",       # keeps classifier shape
                "city": f"{borough} (London)",
                "title": desc[:120],
                "body": f"Planning application at {addr}"
                        f"{f' ({ward} ward)' if ward else ''}: {desc[:800]} "
                        f"[size: {rec.get('app_size') or 'n/a'}, "
                        f"status: {rec.get('app_state') or 'n/a'}, "
                        f"public comments: {n if rec.get('n_comments') is not None else 'not published'}]",
                "score": n,
                "num_comments": n,
                "permalink": rec.get("url") or rec.get("link") or "",
                "created_utc": time.time(),
                "platform": "planning",
                "source_type": "planning",
                "area": ward or addr[:60],
                "postcode": (rec.get("postcode") or "").strip(),
            })
        kept.sort(key=lambda k: k["num_comments"], reverse=True)
        out += kept[:cfg["max_per_borough"]]
        log.info("PlanIt %s: %d recent -> %d interesting (kept %d)",
                 borough, len(records), len(kept), min(len(kept), cfg["max_per_borough"]))
    log.info("PlanIt: %d items total", len(out))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conn = db.connect()
    items = scrape(conn)
    print(f"\n{len(items)} planning items:")
    for it in items:
        print(f"- [{it['city']}] {it['title']}")
        print(f"  comments: {it['num_comments']} | {it['permalink']}")
    conn.close()
