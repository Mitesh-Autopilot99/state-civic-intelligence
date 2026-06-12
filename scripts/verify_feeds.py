"""Probe candidate RSS/Atom feeds and set status to 'verified' or 'dead'.
Same candidate->verified pattern as verify_targets.py.

Covers BOTH config files, each written back separately:
  config/targets.yaml          (pilot, hand-maintained)
  config/targets_national.yaml (generated — may hold hundreds of candidates)
Sections probed: fixmystreet.councils[].feed and council_news.feeds[].url.

HTTP 429 (rate-limited) leaves a feed as 'candidate' so the next run retries it
instead of wrongly burying it as dead. Entries already 'dropped' are skipped.

Run on the Mac (needs open internet):  python scripts/verify_feeds.py
With the national config present the first run probes ~700 feeds at 1/s — allow
~20-30 minutes (progress is saved every 25 probes, so it's safe to interrupt
and re-run). Re-runs only probe candidates; --recheck probes everything.
"""
import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = [PROJECT_ROOT / "config" / "targets.yaml",
           PROJECT_ROOT / "config" / "targets_national.yaml"]
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
    return root.tag.rsplit("}", 1)[-1].lower() in ("feed", "rss", "rdf")


def probe(url: str) -> tuple[str, str]:
    try:
        r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
        if r.status_code == 429:
            return "candidate", "HTTP 429 (rate limited — will retry next run)"
        if r.status_code != 200:
            return "dead", f"HTTP {r.status_code}"
        if not is_feed(r.text):
            return "dead", "200 but not RSS/Atom XML"
        n = r.text.count("<item") + r.text.count("<entry")
        return "verified", f"OK ({n} entries)"
    except Exception as e:
        return "dead", str(e)[:80]


def _entries(cfg: dict):
    """Yield (section_label, entry_dict, url_key) for everything probeable."""
    for c in (cfg.get("fixmystreet") or {}).get("councils", []):
        yield "fixmystreet", c, "feed"
    for f in (cfg.get("council_news") or {}).get("feeds", []):
        yield f.get("kind", "council_news"), f, "url"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recheck", action="store_true",
                    help="probe ALL feeds, not just candidates")
    args = ap.parse_args()

    grand_dead = grand_total = 0
    for config in CONFIGS:
        if not config.exists():
            continue
        cfg = yaml.safe_load(config.read_text())
        changed, rows = False, []
        todo = list(_entries(cfg))
        probe_list = [(s, e, k) for s, e, k in todo
                      if e.get("status") != "dropped"
                      and (args.recheck or e.get("status") in (None, "candidate"))]
        skipped = len(todo) - len(probe_list)
        print(f"\n=== {config.name}: probing {len(probe_list)} feeds "
              f"({skipped} skipped — already decided or dropped) ===")
        for i, (src, entry, key) in enumerate(probe_list, 1):
            status, note = probe(entry[key])
            rows.append((src, entry.get("name", "?"), entry[key], status, note))
            if entry.get("status") != status:
                entry["status"], changed = status, True
            if i % 25 == 0:
                ok_so_far = sum(1 for r in rows if r[3] == "verified")
                print(f"  ...{i}/{len(probe_list)} probed ({ok_so_far} verified so far)")
                config.write_text(yaml.safe_dump(cfg, sort_keys=False,
                                                 allow_unicode=True, width=1000))
            time.sleep(1)

        # full table only for small runs; summary otherwise (700 rows is noise)
        if len(rows) <= 60:
            print(f"\n{'source':<14} {'name':<30} {'status':<10} note")
            for src, name, url, status, note in rows:
                print(f"{src:<14} {str(name)[:28]:<30} {status:<10} {note}")
                print(f"{'':14} {url}")
        else:
            print("\nDead feeds only (everything else verified or retrying):")
            for src, name, url, status, note in rows:
                if status == "dead":
                    print(f"  {src:<14} {str(name)[:28]:<30} {note}")
        if changed:
            config.write_text(yaml.safe_dump(cfg, sort_keys=False,
                                             allow_unicode=True, width=1000))
            print(f"\n{config.name} updated.")
        ok = sum(1 for r in rows if r[3] == "verified")
        dead = sum(1 for r in rows if r[3] == "dead")
        retry = sum(1 for r in rows if r[3] == "candidate")
        print(f"{config.name}: {ok} verified, {dead} dead, {retry} rate-limited (retry later)")
        grand_dead += dead
        grand_total += len(rows)

    sys.exit(1 if grand_total and grand_dead == grand_total else 0)


if __name__ == "__main__":
    main()
