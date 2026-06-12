"""PlanIt source (planit.org.uk — free national aggregator of planning registers).

Pulls recent planning applications for the five pilot boroughs, prioritising
ones with public comments (n_comments) — the research adjustment: PlanIt exposes
a comment COUNT, not a separate objection count, and it's optional. Where it is
missing we fall back to app_size (Large/Medium) and contention keywords.

GDPR: the `select` clause requests ONLY the fields below. Applicant/agent/case
officer fields are never requested (PlanIt doesn't store the names anyway).

Fair use: one request per borough per day, pg_sz<=100 — far inside the
documented limits (5000 results / 1MB / 429+Retry-After on excess).

National mode: PlanIt rate-limits long request bursts, so we never try all
~380 councils in one run. A persistent cursor (planit_cursor table) rotates
through the list max_boroughs_per_run at a time (default 60/day -> full UK
cycle ~1 week) and the lookback window is widened automatically to cover the
cycle, so nothing is missed. A second 429 in a run ends the PlanIt pass for
the day — no strike recorded, the cursor stays put, tomorrow resumes there.

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
import config_loader  # noqa: E402

log = logging.getLogger("planit")

API = "https://www.planit.org.uk/api/applics/json"
HTTP_TIMEOUT = 60
SLEEP_BETWEEN = 4          # seconds between borough requests (politeness)
# ONLY these fields are requested; '->' lifts two safe keys out of other_fields
SELECT = ("name,uid,area_name,start_date,address,description,postcode,"
          "app_state,app_size,app_type,link,url,"
          "n_comments:other_fields->n_comments,ward_name:other_fields->ward_name")
# contention cues used only when a council publishes no comment counts
KEYWORDS = ["demolition", "demolish", "tower", "storey", "flats", "hmo",
            "telecommunications", "mast", "5g", "takeaway", "betting",
            "late night", "licence", "car park", "loss of", "change of use"]


STRIKES_SCHEMA = """
CREATE TABLE IF NOT EXISTS planit_strikes (
    area TEXT PRIMARY KEY,
    fails INTEGER,
    last_fail TEXT
);
"""

CURSOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS planit_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_idx INTEGER
);
"""


class RateLimited(Exception):
    """PlanIt said 429 twice — stop the whole pass, resume next run."""


def load_config() -> dict:
    cfg = config_loader.load_targets()
    p = cfg.get("planit") or {}
    return {
        "enabled": p.get("enabled", True),
        "boroughs": p.get("boroughs", ["Croydon", "Lewisham", "Hackney",
                                       "Waltham Forest", "Ealing"]),
        "recent_days": int(p.get("recent_days", 3)),
        "pg_sz": int(p.get("pg_sz", 100)),
        "min_comments": int(p.get("min_comments", 3)),
        "max_per_borough": int(p.get("max_per_borough", 8)),
        # areas polled per run; lists shorter than this (pilot) do a full pass
        "max_boroughs_per_run": int(p.get("max_boroughs_per_run", 60)),
        # label -> region map from the national config (empty in pilot-only mode)
        "regions": cfg.get("regions") or {},
    }


def _label(borough: str, regions: dict) -> str:
    """City label for the mapper. Pilot-only config (no regions map) keeps the
    historical ' (London)' suffix; nationally only London boroughs get it."""
    if not regions:
        return f"{borough} (London)"
    return f"{borough} (London)" if regions.get(borough) == "London" else borough


def _struck_out(conn, area: str) -> bool:
    """True if this area failed 3+ times recently — retry weekly, not daily."""
    row = conn.execute("SELECT fails, last_fail FROM planit_strikes WHERE area=?",
                       (area,)).fetchone()
    if not row or row["fails"] < 3:
        return False
    from datetime import datetime, timedelta
    return row["last_fail"] > (datetime.utcnow() - timedelta(days=7)).isoformat()


def _cursor(conn) -> int:
    row = conn.execute("SELECT next_idx FROM planit_cursor WHERE id=1").fetchone()
    return row["next_idx"] if row else 0


def _save_cursor(conn, idx: int):
    conn.execute("""INSERT INTO planit_cursor VALUES (1, ?)
                    ON CONFLICT(id) DO UPDATE SET next_idx = ?""", (idx, idx))
    conn.commit()


def _auth_variants(borough: str) -> list[str]:
    """PlanIt's authority names don't always match mySociety nice-names
    (e.g. 'Aberdeen City' -> 'Aberdeen'). Tried in order on a 400."""
    out = [borough]
    for cand in (borough.removeprefix("City of ").strip(),
                 borough.replace(" City", "").strip(),
                 borough.split(",")[0].strip()):
        if cand and cand not in out:
            out.append(cand)
    return out


def _params(auth: str, recent: int, cfg: dict) -> dict:
    return {"auth": auth, "recent": recent, "pg_sz": cfg["pg_sz"],
            "select": SELECT, "sort": "start_date.desc.nullslast",
            "compress": "on"}


def _fetch_borough(borough: str, cfg: dict, recent: int) -> list[dict]:
    last = None
    for auth in _auth_variants(borough):
        r = requests.get(API, params=_params(auth, recent, cfg),
                         timeout=HTTP_TIMEOUT)
        if r.status_code == 429:
            wait = min(int(r.headers.get("Retry-After", 60)), 120)
            log.warning("PlanIt 429 at %s — waiting %ss once", borough, wait)
            time.sleep(wait)
            r = requests.get(API, params=_params(auth, recent, cfg),
                             timeout=HTTP_TIMEOUT)
            if r.status_code == 429:
                raise RateLimited(borough)
        if r.status_code == 400:        # unknown auth name — try next variant
            last = r
            continue
        r.raise_for_status()
        return r.json().get("records", [])
    last.raise_for_status()             # all variants rejected -> strike


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
    conn.executescript(STRIKES_SCHEMA)
    conn.executescript(CURSOR_SCHEMA)
    seen = {row["post_id"] for row in conn.execute("SELECT post_id FROM seen_posts")}
    boroughs = cfg["boroughs"]
    if not boroughs:
        return []
    n = len(boroughs)
    per_run = min(cfg["max_boroughs_per_run"], n)
    start = _cursor(conn) % n
    todo = [boroughs[(start + i) % n] for i in range(per_run)]
    recent = cfg["recent_days"]
    if per_run < n:  # rotating: widen lookback to cover one full cycle + slack
        recent = max(recent, -(-n // per_run) + 1)
    out = []
    skipped_struck = 0
    processed = 0
    for i, borough in enumerate(todo):
        if _struck_out(conn, borough):
            skipped_struck += 1
            processed += 1
            continue
        if i:
            time.sleep(SLEEP_BETWEEN)
        try:
            records = _fetch_borough(borough, cfg, recent)
        except RateLimited:
            log.warning("PlanIt rate-limited — ending this pass; "
                        "resuming at %s next run", borough)
            break
        except Exception as e:
            log.error("PlanIt fetch failed for %s: %s", borough, e)
            conn.execute("""INSERT INTO planit_strikes VALUES (?, 1, datetime('now'))
                            ON CONFLICT(area) DO UPDATE SET
                            fails = fails + 1, last_fail = datetime('now')""",
                         (borough,))
            conn.commit()
            processed += 1
            continue
        processed += 1
        conn.execute("DELETE FROM planit_strikes WHERE area=?", (borough,))
        conn.commit()
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
                "city": _label(borough, cfg["regions"]),
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
    _save_cursor(conn, (start + processed) % n)
    if per_run < n:
        log.info("PlanIt: rotation — %d/%d areas this run "
                 "(full cycle ~%d runs, lookback %dd)",
                 processed, n, -(-n // per_run), recent)
    if skipped_struck:
        log.info("PlanIt: skipped %d struck-out areas (3+ recent failures, weekly retry)",
                 skipped_struck)
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
