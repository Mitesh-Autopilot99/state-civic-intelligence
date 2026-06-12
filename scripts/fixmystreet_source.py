"""FixMyStreet source (fixmystreet.com per-council RSS feeds).

The open FMS research dataset ends at 2020 (research finding), so we build our
OWN baseline: poll each borough's RSS feed daily, keyword-classify report
titles in-memory, and store ONLY (day, council, category, count) into
fms_daily_counts. Trend items ("pothole reports in Croydon up 3x") are emitted
once we have >= baseline_days of history and the last 7 days run at
>= trend_ratio x the prior baseline rate.

GDPR: report titles are read in-memory for keyword counting and then discarded.
No titles, no reporter names, no links to individual reports are stored —
only aggregate counts. The brief links to the council's public reports page.

Trend items are emitted PRE-CLASSIFIED (category/urgency/etc. already set, no
LLM tokens spent) — classifier.classify() passes them straight through.

Standalone test:  python scripts/fixmystreet_source.py
"""
import logging
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import db  # noqa: E402

log = logging.getLogger("fixmystreet")
HTTP_TIMEOUT = 30
SLEEP_BETWEEN = 1
UA = {"User-Agent": "state-civic-listener/1.0 (aggregate trend monitoring; contact thakermitesh89@gmail.com)"}

COUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS fms_daily_counts (
    day TEXT,           -- YYYY-MM-DD
    council TEXT,
    category TEXT,
    count INTEGER,
    PRIMARY KEY (day, council, category)
);
"""

# deterministic keyword -> category map (no LLM tokens, no raw text stored)
CATEGORY_KEYWORDS = {
    "potholes_roads": ["pothole", "road surface", "carriageway", "tarmac", "kerb",
                       "pavement", "paving", "footpath", "road marking", "speed bump"],
    "bins_waste": ["fly tipping", "fly-tipping", "flytipping", "rubbish", "litter",
                   "bin", "waste", "dumped", "mattress", "refuse"],
    "parks_environment": ["tree", "park", "grass", "overgrown", "weeds", "hedge",
                          "graffiti", "dog fouling", "flower bed"],
    "transport": ["street light", "streetlight", "traffic light", "crossing",
                  "parking", "sign", "bus stop", "cycle", "bollard", "zebra"],
    "safety_crime": ["abandoned vehicle", "abandoned car", "vandal", "broken glass",
                     "needle", "drug", "unsafe"],
    "council_services": ["drain", "flood", "gully", "blocked", "leak", "manhole",
                         "sewage", "water"],
}


def load_config() -> dict:
    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "targets.yaml").read_text())
    f = cfg.get("fixmystreet") or {}
    return {
        "enabled": f.get("enabled", True),
        "trend_ratio": float(f.get("trend_ratio", 2.0)),
        "min_reports_7d": int(f.get("min_reports_7d", 5)),
        "baseline_days": int(f.get("baseline_days", 14)),
        "councils": [c for c in f.get("councils", []) if c.get("status") == "verified"],
    }


def _categorise(title: str) -> str:
    t = title.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in t for k in kws):
            return cat
    return "other"


def _parse_feed(xml_text: str) -> list[dict]:
    """Return [{title, guid}] from RSS or Atom. Titles stay in-memory only."""
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title, guid = "", ""
        for child in item:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "title":
                title = (child.text or "").strip()
            elif ctag in ("guid", "id"):
                guid = (child.text or "").strip()
            elif ctag == "link" and not guid:
                guid = (child.text or child.get("href") or "").strip()
        if title:
            out.append({"title": title, "guid": guid or title})
    return out


def _record_counts(conn, council: str, entries: list[dict]):
    """Count NEW reports per category for today; discard titles immediately."""
    seen = {r["post_id"] for r in conn.execute("SELECT post_id FROM seen_posts")}
    today = str(date.today())
    counts: dict[str, int] = {}
    for e in entries:
        pid = f"fms:{e['guid']}"
        if pid in seen:
            continue
        counts[_categorise(e["title"])] = counts.get(_categorise(e["title"]), 0) + 1
        conn.execute("INSERT OR IGNORE INTO seen_posts VALUES (?, datetime('now'))", (pid,))
    for cat, n in counts.items():
        conn.execute("""INSERT INTO fms_daily_counts VALUES (?,?,?,?)
                        ON CONFLICT(day, council, category)
                        DO UPDATE SET count = count + excluded.count""",
                     (today, council, cat, n))
    conn.commit()


def _trends(conn, cfg: dict) -> list[dict]:
    """Compare last 7 days vs the prior baseline window, per council+category."""
    out = []
    today = date.today()
    d7 = str(today - timedelta(days=7))
    d28 = str(today - timedelta(days=28))
    for c in cfg["councils"]:
        council = c["name"]
        first = conn.execute("SELECT MIN(day) m FROM fms_daily_counts WHERE council=?",
                             (council,)).fetchone()["m"]
        if not first or (today - date.fromisoformat(first)).days < cfg["baseline_days"]:
            log.info("FMS %s: building baseline since %s — no trends yet", council, first)
            continue
        rows = conn.execute("""
            SELECT category,
                   SUM(CASE WHEN day >  ? THEN count ELSE 0 END) AS recent,
                   SUM(CASE WHEN day <= ? AND day > ? THEN count ELSE 0 END) AS base,
                   COUNT(DISTINCT CASE WHEN day <= ? AND day > ? THEN day END) AS base_days
            FROM fms_daily_counts WHERE council=? GROUP BY category""",
            (d7, d7, d28, d7, d28, council)).fetchall()
        for r in rows:
            if r["category"] == "other" or not r["base_days"]:
                continue
            base_rate7 = (r["base"] / r["base_days"]) * 7
            if r["recent"] < cfg["min_reports_7d"] or r["recent"] < base_rate7 * cfg["trend_ratio"]:
                continue
            ratio = round(r["recent"] / max(base_rate7, 0.1), 1)
            week = today.isocalendar()
            pid = f"fms-trend:{council}:{r['category']}:{week.year}w{week.week}"
            if conn.execute("SELECT 1 FROM seen_posts WHERE post_id=?", (pid,)).fetchone():
                continue
            cat_label = r["category"].replace("_", "/")
            out.append({
                "id": pid,
                "subreddit": "fixmystreet", "city": c.get("label", council),
                "title": f"{cat_label} reports rising in {council}",
                "body": "",
                "score": int(r["recent"]), "num_comments": 0,
                "permalink": c.get("reports_url",
                                   f"https://www.fixmystreet.com/reports/{council.replace(' ', '+')}"),
                "created_utc": time.time(),
                "platform": "fixmystreet", "source_type": "fixmystreet",
                # pre-classified: no LLM needed for an aggregate trend
                "preclassified": True,
                "category": r["category"],
                "urgency": min(2 + int(ratio >= 3) + int(ratio >= 5), 5),
                "specificity": 3,
                "area": council,
                "summary": (f"{cat_label} reports in {council} up {ratio}x: "
                            f"{r['recent']} in the last 7 days vs ~{round(base_rate7)} "
                            f"expected from the prior {r['base_days']}-day baseline"),
            })
    return out


def scrape(conn) -> list:
    cfg = load_config()
    if not cfg["enabled"]:
        log.info("FixMyStreet source disabled in config — skipping.")
        return []
    if not cfg["councils"]:
        log.warning("No VERIFIED fixmystreet councils in config — run "
                    "scripts/verify_feeds.py first. Skipping.")
        return []
    conn.executescript(COUNTS_SCHEMA)
    for c in cfg["councils"]:
        try:
            r = requests.get(c["feed"], headers=UA, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            entries = _parse_feed(r.text)
            _record_counts(conn, c["name"], entries)
            log.info("FMS %s: %d feed entries counted", c["name"], len(entries))
        except Exception as e:
            log.error("FMS feed failed for %s: %s", c["name"], e)
        time.sleep(SLEEP_BETWEEN)
    items = _trends(conn, cfg)
    for it in items:   # one brief item per trend per ISO week
        conn.execute("INSERT OR IGNORE INTO seen_posts VALUES (?, datetime('now'))", (it["id"],))
    conn.commit()
    log.info("FixMyStreet: %d trend items", len(items))
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conn = db.connect()
    items = scrape(conn)
    print(f"\n{len(items)} trend items:")
    for it in items:
        print(f"- [{it['area']}] {it['summary']}")
    rows = conn.execute("SELECT day, council, category, count FROM fms_daily_counts "
                        "ORDER BY day DESC LIMIT 12").fetchall()
    print("\nLatest daily counts (aggregates only — this is ALL we store):")
    for r in rows:
        print(f"  {r['day']} {r['council']:<16} {r['category']:<20} {r['count']}")
    conn.close()
