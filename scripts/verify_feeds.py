"""Probe candidate RSS/Atom feeds in config/targets.yaml and set their status
to 'verified' or 'dead'. Same candidate->verified pattern as verify_targets.py.
Covers: fixmystreet.councils[].feed and council_news.feeds[].url.

Run ONCE on the Mac (needs open internet):  python scripts/verify_feeds.py
The pipeline only polls feeds with status: verified.
"""
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / "config" / "targets.yaml"
UA = {"User-Agent": "state-civic-listener/1.0 (feed verification; contact thakermitesh89@gmail.com)"}


def is_feed(text: str) -> bool:
    # ModernGov serves a UTF-8 BOM; requests can mis-decode it as 'ï»¿'.
    # Either form makes ET.fromstring raise — strip both before parsing.
    text = text.lstrip('\ufeff')
    if text.startswith("ï»¿"):
        text = text[3:]
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return False
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag == "feed":          # Atom
        return True
    if tag in ("rss", "rdf"):  # RSS 2.0 / 1.0
        return True
    return False


def probe(url: str) -> tuple[str, str]:
    try:
        r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            return "dead", f"HTTP {r.status_code}"
        if not is_feed(r.text):
            return "dead", "200 but not RSS/Atom XML"
        n = r.text.count("<item") + r.text.count("<entry")
        return "verified", f"OK ({n} entries)"
    except Exception as e:
        return "dead", str(e)[:80]


def main():
    cfg = yaml.safe_load(CONFIG.read_text())
    changed = False
    rows = []
    for c in (cfg.get("fixmystreet") or {}).get("councils", []):
        status, note = probe(c["feed"])
        rows.append(("fixmystreet", c["name"], c["feed"], status, note))
        if c.get("status") != status:
            c["status"], changed = status, True
        time.sleep(1)
    for f in (cfg.get("council_news") or {}).get("feeds", []):
        status, note = probe(f["url"])
        rows.append((f.get("kind", "council_news"), f["name"], f["url"], status, note))
        if f.get("status") != status:
            f["status"], changed = status, True
        time.sleep(1)

    print(f"\n{'source':<14} {'name':<28} {'status':<9} note")
    for src, name, url, status, note in rows:
        print(f"{src:<14} {name:<28} {status:<9} {note}")
        print(f"{'':14} {url}")
    if changed:
        CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
        print("\nconfig/targets.yaml updated.")
    else:
        print("\nNo status changes.")
    dead = [r for r in rows if r[3] == "dead"]
    sys.exit(1 if dead and len(dead) == len(rows) else 0)


if __name__ == "__main__":
    main()
