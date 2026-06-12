"""Offline test for fixmystreet_source.py — fixture RSS in the standard
FixMyStreet feed format, synthetic 28-day baseline. No network needed.
Run:  python scripts/test_fixmystreet_offline.py"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
os.environ["STATE_INTEL_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")

import db                  # noqa: E402
import classifier          # noqa: E402
import fixmystreet_source as fms  # noqa: E402

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>New reports on FixMyStreet</title>
<item><title>Pothole outside 14 Brighton Road</title>
<link>https://www.fixmystreet.com/report/9000001</link>
<guid>https://www.fixmystreet.com/report/9000001</guid></item>
<item><title>Deep pothole near bus stop</title>
<link>https://www.fixmystreet.com/report/9000002</link>
<guid>https://www.fixmystreet.com/report/9000002</guid></item>
<item><title>Fly tipping on Catford Hill</title>
<link>https://www.fixmystreet.com/report/9000003</link>
<guid>https://www.fixmystreet.com/report/9000003</guid></item>
</channel></rss>"""


class FakeResp:
    text = RSS
    def raise_for_status(self): pass


fms.requests.get = lambda *a, **k: FakeResp()
fms.SLEEP_BETWEEN = 0
CFG = {
    "enabled": True, "trend_ratio": 2.0, "min_reports_7d": 5, "baseline_days": 14,
    "councils": [{"name": "Croydon", "label": "Croydon (London)",
                  "feed": "https://x/rss", "status": "verified"}],
}
fms.load_config = lambda: CFG

conn = db.connect()
conn.executescript(fms.COUNTS_SCHEMA)
today = date.today()
# 28-day history: potholes_roads baseline ~1/day, then a spike in the last 7 days
for d in range(28, 0, -1):
    day = str(today - timedelta(days=d))
    n = 4 if d <= 7 else 1   # 7-day total 28... wait: d<=7 gives last 7 days
    conn.execute("INSERT OR REPLACE INTO fms_daily_counts VALUES (?,?,?,?)",
                 (day, "Croydon", "potholes_roads", n))
    conn.execute("INSERT OR REPLACE INTO fms_daily_counts VALUES (?,?,?,?)",
                 (day, "Croydon", "bins_waste", 2))     # flat -> no trend
conn.commit()

items = fms.scrape(conn)

row = conn.execute("SELECT count FROM fms_daily_counts WHERE day=? AND council=? "
                   "AND category='potholes_roads'", (str(today), "Croydon")).fetchone()
trend = next((i for i in items if i["category"] == "potholes_roads"), None)
checks = {
    "feed entries counted into aggregates": row is not None and row["count"] == 2,
    "no raw titles stored anywhere": not conn.execute(
        "SELECT 1 FROM fms_daily_counts WHERE category LIKE '%Brighton%'").fetchone(),
    "spiking category emits trend item": trend is not None,
    "flat category emits NO trend": not any(i["category"] == "bins_waste" for i in items),
    "trend summary human-readable": trend and "up" in trend["summary"] and "Croydon" in trend["summary"],
    "preclassified (no LLM tokens)": trend and trend.get("preclassified") is True,
    "classifier passes it straight through": trend in classifier.classify([trend]),
    "source_type tagged": all(i["source_type"] == "fixmystreet" for i in items),
    "weekly dedupe: second run emits 0": len(fms._trends(conn, CFG)) == 0,
}

print()
ok = True
for name, passed in checks.items():
    print(("PASS  " if passed else "FAIL  ") + name)
    ok &= passed
print(f"\nTrend items: {len(items)}")
for i in items:
    print(f"  {i['summary']}")
sys.exit(0 if ok else 1)
