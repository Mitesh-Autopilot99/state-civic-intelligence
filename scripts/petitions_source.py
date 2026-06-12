"""UK Parliament petitions source (petition.parliament.uk, public JSON, no auth).

Finds open petitions that OVER-INDEX in our target constituencies:
local signature share >= over_index_ratio x the petition's national
per-constituency average (total / 650). Those are direct, structured evidence
of what a constituency cares about.

GDPR: the API exposes zero personal data — signature counts only. MP names are
public officials and come straight from the API (no separate lookup needed).

Frugality: one list pass per day (paged, filtered by signature_floor), then one
detail fetch per candidate petition — skipped when our petition_checks cache
says the count hasn't moved >=10% in the last 7 days.

Output: same post-dict shape as reddit/facebook scrapers, with constituency and
mp_name PRE-SET (constituency_mapper skips these) and source_type='petition'.

Standalone test:  python scripts/petitions_source.py
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

log = logging.getLogger("petitions")

BASE = "https://petition.parliament.uk"
LIST_URL = f"{BASE}/petitions.json"
N_CONSTITUENCIES = 650
MAX_LIST_PAGES = 20
HTTP_TIMEOUT = 30
SLEEP_BETWEEN = 0.5          # polite spacing; no documented limit, stay gentle

CHECKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS petition_checks (
    petition_id TEXT PRIMARY KEY,
    sig_count INTEGER,
    checked_at TEXT
);
"""


def load_config() -> dict:
    cfg = config_loader.load_targets()
    pcfg = cfg.get("petitions") or {}
    return {
        "enabled": pcfg.get("enabled", True),
        "signature_floor": int(pcfg.get("signature_floor", 500)),
        "over_index_ratio": float(pcfg.get("over_index_ratio", 2.0)),
        "min_local_signatures": int(pcfg.get("min_local_signatures", 25)),
        "max_detail_fetches": int(pcfg.get("max_detail_fetches", 150)),
        # national mode: every constituency is a target — skip the name filter
        # and instead keep only the top-N most over-indexing constituencies
        # per petition, so one viral petition can't flood the brief.
        "national": bool(pcfg.get("national", False)),
        "max_per_petition": int(pcfg.get("max_constituencies_per_petition", 5)),
        "targets": [t.lower() for t in cfg.get("target_constituencies", [])],
    }


def _list_open_petitions(floor: int) -> list[dict]:
    """One paged pass over open petitions; keep id + total count above floor."""
    out, url, pages = [], f"{LIST_URL}?state=open", 0
    while url and pages < MAX_LIST_PAGES:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        for p in j.get("data", []):
            attrs = p.get("attributes", {})
            count = int(attrs.get("signature_count") or 0)
            if count >= floor:
                out.append({"id": str(p.get("id")), "count": count,
                            "action": attrs.get("action", "")})
        url = (j.get("links") or {}).get("next")
        pages += 1
        time.sleep(SLEEP_BETWEEN)
    log.info("Petitions list: %d open petitions >= %d signatures (%d pages)",
             len(out), floor, pages)
    return out


def _needs_check(conn, pid: str, count: int) -> bool:
    row = conn.execute("SELECT sig_count, checked_at FROM petition_checks "
                       "WHERE petition_id=?", (pid,)).fetchone()
    if row is None:
        return True
    grown = count >= row["sig_count"] * 1.10
    stale = row["checked_at"] < _days_ago_iso(7)
    return grown or stale


def _days_ago_iso(days: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def _detail(pid: str) -> dict:
    r = requests.get(f"{BASE}/petitions/{pid}.json", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json().get("data", {}).get("attributes", {})


def scrape(conn) -> list:
    """Return post-shaped dicts for petitions over-indexing in target constituencies."""
    cfg = load_config()
    if not cfg["enabled"]:
        log.info("Petitions source disabled in config — skipping.")
        return []
    if not cfg["national"] and not cfg["targets"]:
        log.warning("No target_constituencies in config — skipping.")
        return []
    conn.executescript(CHECKS_SCHEMA)

    candidates = _list_open_petitions(cfg["signature_floor"])
    seen = {row["post_id"] for row in conn.execute("SELECT post_id FROM seen_posts")}
    out, fetches = [], 0
    for c in candidates:
        if fetches >= cfg["max_detail_fetches"]:
            log.info("Detail-fetch cap (%d) reached; rest waits for tomorrow.",
                     cfg["max_detail_fetches"])
            break
        if not _needs_check(conn, c["id"], c["count"]):
            continue
        try:
            attrs = _detail(c["id"])
        except Exception as e:
            log.warning("Detail fetch failed for petition %s: %s", c["id"], e)
            continue
        fetches += 1
        time.sleep(SLEEP_BETWEEN)
        conn.execute("INSERT OR REPLACE INTO petition_checks VALUES (?,?,datetime('now'))",
                     (c["id"], c["count"]))

        total = int(attrs.get("signature_count") or c["count"])
        avg = total / N_CONSTITUENCIES
        by_const = attrs.get("signatures_by_constituency") or []

        # collect every qualifying constituency row first, then (national mode)
        # keep only the most over-indexing few so one petition can't flood the brief
        qualifying = []
        for row in by_const:
            name = (row.get("name") or "").strip()
            if not cfg["national"] and name.lower() not in cfg["targets"]:
                continue
            local = int(row.get("signature_count") or 0)
            if local < cfg["min_local_signatures"] or local < avg * cfg["over_index_ratio"]:
                continue
            qualifying.append((local / max(avg, 0.01), local, name, row))
        if cfg["national"]:
            qualifying.sort(key=lambda q: q[0], reverse=True)
            qualifying = qualifying[:cfg["max_per_petition"]]

        for raw_ratio, local, name, row in qualifying:
            post_id = f"petition:{c['id']}:{row.get('ons_code') or name}"
            if post_id in seen:
                continue
            ratio = round(raw_ratio, 1)
            action = attrs.get("action", "")
            background = (attrs.get("background") or "")[:600]
            out.append({
                "id": post_id,
                "subreddit": "petition.parliament.uk",   # keeps classifier shape
                "city": name,
                "title": action[:120],
                "body": f"{action}. {background} "
                        f"[{local} signatures in {name} — {ratio}x the national "
                        f"per-constituency average; {total} UK-wide]",
                "score": local,                # local signatures = engagement
                "num_comments": 0,
                "permalink": f"{BASE}/petitions/{c['id']}",
                "created_utc": time.time(),
                "platform": "petition",
                "source_type": "petition",
                # pre-resolved — mapper will skip geocoding for these
                "constituency": name,
                "mp_name": row.get("mp") or "",
            })
    conn.commit()
    log.info("Petitions: %d candidates -> %d detail fetches -> %d over-indexing "
             "constituency items", len(candidates), fetches, len(out))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conn = db.connect()
    items = scrape(conn)
    print(f"\n{len(items)} over-indexing petition items:")
    for it in items:
        print(f"- [{it['constituency']}] {it['title']}")
        print(f"  {it['body'][it['body'].rfind('['):]}")
        print(f"  MP: {it['mp_name']} | {it['permalink']}")
    conn.close()
