"""Offline test for council_news_source.py — RSS (WordPress) + Atom (ModernGov
style) fixtures. No network needed. Run:  python scripts/test_council_news_offline.py"""
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
os.environ["STATE_INTEL_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")

import db                   # noqa: E402
import council_news_source as cn  # noqa: E402

NEWS_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Council approves controversial housing development on Purley Way</title>
<link>https://insidecroydon.com/2026/06/11/purley-way/</link>
<dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">A Journalist</dc:creator></item>
<item><title>Crystal Palace win at the weekend delights fans</title>
<link>https://insidecroydon.com/2026/06/10/palace/</link></item>
<item><title>Fly-tipping complaints surge in Thornton Heath</title>
<link>https://insidecroydon.com/2026/06/10/flytipping/</link></item>
</channel></rss>"""

AGENDA_ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<title>Lewisham Council - Browse meetings</title>
<entry><title>Planning Committee A - Agenda published, 18/06/2026</title>
<link href="https://councilmeetings.lewisham.gov.uk/ieListDocuments.aspx?CId=139"/>
<id>https://councilmeetings.lewisham.gov.uk/ieListDocuments.aspx?CId=139</id></entry>
</feed>"""

FEEDS = {
    "https://news/feed": NEWS_RSS,
    "https://agenda/feed": AGENDA_ATOM,
}


class FakeResp:
    def __init__(self, text): self.text = text
    def raise_for_status(self): pass


cn.requests.get = lambda url, **k: FakeResp(FEEDS[url])
cn.SLEEP_BETWEEN = 0
cn.load_config = lambda: (
    [{"name": "inside-croydon", "kind": "local_news", "label": "Croydon (London)",
      "url": "https://news/feed", "status": "verified"},
     {"name": "lewisham-democracy", "kind": "council_agenda", "label": "Lewisham (London)",
      "url": "https://agenda/feed", "status": "verified"}],
    ["housing", "fly-tipping", "planning"],
)

conn = db.connect()
items = cn.scrape(conn)
titles = [i["title"] for i in items]

checks = {
    "civic headline kept (keyword match)": any("housing development" in t for t in titles),
    "non-civic headline dropped (football)": not any("Crystal Palace" in t for t in titles),
    "second keyword headline kept": any("Fly-tipping" in t for t in titles),
    "agenda item kept WITHOUT keyword gate": any("Planning Committee A" in t for t in titles),
    "Atom (ModernGov) parsing works": any(i["source_type"] == "council_agenda" for i in items),
    "kinds tagged correctly": {i["source_type"] for i in items} == {"local_news", "council_agenda"},
    "headlines only — no bodies": all(i["body"] == "" for i in items),
    "GDPR: bylines never read or stored": all(
        "Journalist" not in str(i.values()) for i in items),
    "every item has a source link": all(i["permalink"].startswith("https://") for i in items),
}
for i in items:
    conn.execute("INSERT OR IGNORE INTO seen_posts VALUES (?, datetime('now'))", (i["id"],))
conn.commit()
checks["dedupe: second run yields 0 items"] = len(cn.scrape(conn)) == 0

print()
ok = True
for name, passed in checks.items():
    print(("PASS  " if passed else "FAIL  ") + name)
    ok &= passed
print(f"\nItems produced: {len(items)}")
for i in items:
    print(f"  [{i['source_type']}|{i['city']}] {i['title']}")
sys.exit(0 if ok else 1)
