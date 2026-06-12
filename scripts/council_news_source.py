"""Council democracy + local news RSS source (headlines/agenda titles ONLY).

Three kinds of feed, all configured in config/targets.yaml under council_news:
  - kind: council_agenda -> ModernGov/CMIS committee feeds (agendas, decisions)
  - kind: local_news     -> LDRS-partner / independent local titles (/feed)
  - kind: google_news    -> Google News RSS query feed, one per borough
                            (rss/search?q=... — plain query feeds don't redirect,
                            see research_tier2_tier3.md §4). Titles arrive as
                            "Headline - Publisher"; the publisher suffix is
                            stripped before classification (byline/source name
                            never stored). Links are Google redirect URLs and
                            are stored as-is — they resolve for the reader.

Feeds start as status: candidate; scripts/verify_feeds.py probes each once on
the Mac and flips them to verified/dead. Only verified feeds are polled.

GDPR/copyright: we read TITLE + LINK only — never article bodies, never
bylines. Titles are keyword-prefiltered (config/keywords.yaml, same as social)
then classified in-memory; afterwards only the issue summary + link persist.

Standalone test:  python scripts/council_news_source.py
"""
import logging
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import db  # noqa: E402

log = logging.getLogger("council_news")
HTTP_TIMEOUT = 30
SLEEP_BETWEEN = 1
MAX_PER_FEED = 15          # classifier token budget
UA = {"User-Agent": "state-civic-listener/1.0 (headline monitoring; contact thakermitesh89@gmail.com)"}


def load_config() -> tuple[list, list]:
    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "targets.yaml").read_text())
    cn = cfg.get("council_news") or {}
    feeds = [f for f in cn.get("feeds", []) if f.get("status") == "verified"]
    kw = yaml.safe_load((PROJECT_ROOT / "config" / "keywords.yaml").read_text())["keywords"]
    return feeds, [k.lower() for k in kw]


def parse_feed(xml_text: str) -> list[dict]:
    """[{title, link}] from RSS or Atom. Titles + links only — nothing else."""
    # ModernGov serves a UTF-8 BOM; requests can mis-decode it as 'ï»¿'.
    # Either form makes ET.fromstring raise — strip both before parsing.
    xml_text = xml_text.lstrip('\ufeff')
    if xml_text.startswith("ï»¿"):
        xml_text = xml_text[3:]
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        title, link = "", ""
        for child in item:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "title":
                title = (child.text or "").strip()
            elif ctag == "link":
                link = (child.text or child.get("href") or "").strip()
            elif ctag == "guid" and not link:
                link = (child.text or "").strip()
        if title and link:
            out.append({"title": title, "link": link})
    return out


def _matches(title: str, keywords: list, kind: str) -> bool:
    # agenda item titles are sparse ("Planning Committee A — agenda published");
    # they're civic by construction, so agenda feeds skip the keyword gate.
    # local_news AND google_news both go through the gate.
    if kind == "council_agenda":
        return True
    t = title.lower()
    return any(k in t for k in keywords)


def _clean_title(title: str, kind: str) -> str:
    # Google News titles end " - Publisher"; strip it so the classifier sees
    # the headline only and no publisher/byline is ever stored.
    if kind == "google_news" and " - " in title:
        return title.rsplit(" - ", 1)[0].strip()
    return title


def scrape(conn) -> list:
    feeds, keywords = load_config()
    if not feeds:
        log.warning("No VERIFIED council_news feeds in config — run "
                    "scripts/verify_feeds.py first. Skipping.")
        return []
    seen = {row["post_id"] for row in conn.execute("SELECT post_id FROM seen_posts")}
    out = []
    for f in feeds:
        kind = f.get("kind", "local_news")
        try:
            r = requests.get(f["url"], headers=UA, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            entries = parse_feed(r.text)
        except Exception as e:
            log.error("Feed failed %s (%s): %s", f["name"], f["url"], e)
            continue
        time.sleep(SLEEP_BETWEEN)
        kept = 0
        for e in entries:
            pid = f"news:{e['link']}"
            if pid in seen or kept >= MAX_PER_FEED:
                continue
            title = _clean_title(e["title"], kind)
            if not _matches(title, keywords, kind):
                continue
            kept += 1
            out.append({
                "id": pid,
                "subreddit": f["name"],            # keeps classifier shape
                "city": f.get("label", f["name"]),
                "title": title[:160],
                "body": "",                        # headlines only, by design
                "score": 0, "num_comments": 0,
                "permalink": e["link"],
                "created_utc": time.time(),
                "platform": kind, "source_type": kind,
            })
        log.info("%s (%s): %d entries -> %d kept", f["name"], kind, len(entries), kept)
    log.info("council_news: %d items from %d feeds", len(out), len(feeds))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conn = db.connect()
    items = scrape(conn)
    print(f"\n{len(items)} headline items:")
    for it in items:
        print(f"- [{it['source_type']}|{it['city']}] {it['title']}")
        print(f"  {it['permalink']}")
    conn.close()
