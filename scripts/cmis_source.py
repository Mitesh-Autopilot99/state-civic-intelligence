"""CMIS source — committee agendas for councils on CMIS (no RSS).

discover_cmis_feeds.py found 17 councils (Birmingham, Colchester, Dudley,
Warrington...) whose committee system is CMIS, which publishes no usable RSS.
But its meetings-calendar page IS fully server-rendered (Telerik RadScheduler):
every meeting is a div like

    <div ... title="14:00 Planning Committee : Committee Room 1" class="rsApt">

inside a cell whose date header links to "#YYYY-MM-DD". So one GET per council
per day gives the month's meetings — committee name, date, time, room. We keep
key committees (same list as ModernGov discovery) meeting within the next
`days_ahead` days and emit them in the standard post shape with
source_type='council_agenda' (same as ModernGov agenda feeds, so the
classifier/scorer/brief treat them identically).

GDPR: the calendar lists committees, dates and rooms — organisations, never
people. Nothing else on the page is read.

Config (config/targets_national.yaml):
    cmis_agendas:
      sites:
      - {name, label, base, pages: [...], status: candidate|verified}
Only status: verified sites are polled. Flip candidates with:

    python scripts/cmis_source.py --verify   # live-checks each candidate,
                                             # verifies the parser, updates yaml
Standalone test:  python scripts/cmis_source.py
"""
from __future__ import annotations   # py3.9

import argparse
import html as html_mod
import logging
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NATIONAL = PROJECT_ROOT / "config" / "targets_national.yaml"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import db  # noqa: E402
import config_loader  # noqa: E402
from discover_council_feeds import _key  # noqa: E402  (same committee filter)

log = logging.getLogger("cmis")

UA = {"User-Agent": "Mozilla/5.0 (compatible; state-civic-listener/1.0; "
                    "agenda calendar; contact thakermitesh89@gmail.com)"}
HTTP_TIMEOUT = 30
SLEEP_BETWEEN = 2            # seconds between sites (different hosts anyway)
# preferred meetings-page names, most calendar-like first
MEETING_PAGES = ("MeetingCalendar.aspx", "MeetingsCalendar.aspx",
                 "Meetings.aspx")

# ordered token scan over the calendar HTML:
#   group 1 = a date-cell anchor  href="#YYYY-MM-DD"
#   group 2 = an appointment title  title="HH:MM Committee : Location"
_TOKEN = re.compile(
    r'href="#(\d{4}-\d{2}-\d{2})"|title="([^"]+)"[^>]*class="rsApt')
_APT = re.compile(r'^(\d{1,2}:\d{2})\s+(.*)$')


def parse_calendar(html: str) -> list[dict]:
    """[{date, time, committee, location}] from a CMIS meetings page."""
    out, current = [], ""
    for m in _TOKEN.finditer(html):
        if m.group(1):
            current = m.group(1)
            continue
        if not current:
            continue                       # appointment before any date cell
        apt = html_mod.unescape(m.group(2)).strip()
        t = _APT.match(apt)
        when, rest = (t.group(1), t.group(2)) if t else ("", apt)
        committee, _, location = (p.strip() for p in rest.partition(" : "))
        if committee:
            out.append({"date": current, "time": when,
                        "committee": committee, "location": location})
    return out


def meetings_url(site: dict) -> str:
    pages = site.get("pages") or []
    for p in MEETING_PAGES:
        if p in pages:
            return f"{site['base']}/{p}"
    return f"{site['base']}/{pages[0]}" if pages else f"{site['base']}/Meetings.aspx"


def load_config() -> dict:
    cfg = config_loader.load_targets()
    c = cfg.get("cmis_agendas") or {}
    return {
        "enabled": c.get("enabled", True),
        "sites": [s for s in c.get("sites", [])
                  if s.get("status") == "verified"],
        "days_ahead": int(c.get("days_ahead", 8)),
        "max_per_site": int(c.get("max_per_site", 6)),
    }


def _items_for(site: dict, meetings: list[dict], cfg: dict,
               today: date | None = None) -> list[dict]:
    """Key-committee meetings within the lookahead window -> post dicts."""
    today = today or date.today()
    horizon = today + timedelta(days=cfg["days_ahead"])
    out = []
    for m in meetings:
        if not _key(m["committee"]):
            continue
        try:
            d = date.fromisoformat(m["date"])
        except ValueError:
            continue
        if not (today <= d <= horizon):
            continue
        out.append({
            "id": f"cmis:{site['name']}:{m['date']}:"
                  f"{re.sub(r'[^a-z0-9]+', '-', m['committee'].lower())[:40]}",
            "subreddit": f"cmis:{site['name']}",   # keeps classifier shape
            "city": site.get("label", site["name"]),
            "title": f"{m['committee']} meets {d.strftime('%a %d %b')}"[:160],
            "body": f"Upcoming council meeting: {m['committee']} on "
                    f"{d.strftime('%A %d %B %Y')}"
                    f"{' at ' + m['time'] if m['time'] else ''}"
                    f"{' (' + m['location'][:80] + ')' if m['location'] else ''}."
                    f" Agenda published via the council's committee system.",
            "score": 0, "num_comments": 0,
            "permalink": meetings_url(site),
            "created_utc": time.time(),
            "platform": "council_agenda", "source_type": "council_agenda",
            "area": site.get("label", site["name"]),
        })
        if len(out) >= cfg["max_per_site"]:
            break
    return out


def _fetch(site: dict) -> str:
    r = requests.get(meetings_url(site), headers=UA, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text


def scrape(conn) -> list:
    cfg = load_config()
    if not cfg["enabled"]:
        log.info("CMIS source disabled in config — skipping.")
        return []
    if not cfg["sites"]:
        log.info("No VERIFIED cmis_agendas sites — run "
                 "`python scripts/cmis_source.py --verify` once. Skipping.")
        return []
    seen = {row["post_id"] for row in conn.execute("SELECT post_id FROM seen_posts")}
    out = []
    for i, site in enumerate(cfg["sites"]):
        if i:
            time.sleep(SLEEP_BETWEEN)
        try:
            meetings = parse_calendar(_fetch(site))
        except Exception as e:
            log.error("CMIS fetch failed for %s: %s", site["name"], e)
            continue
        items = [it for it in _items_for(site, meetings, cfg)
                 if it["id"] not in seen]
        out += items
        log.info("CMIS %s: %d meetings on calendar -> %d key upcoming",
                 site["name"], len(meetings), len(items))
    log.info("CMIS: %d items total", len(out))
    return out


def verify():
    """Live-check every site in targets_national.yaml: fetch its meetings
    page, run the parser, flip candidate->verified when the calendar module
    is present. Sites that fail keep status candidate plus a note."""
    nat = yaml.safe_load(NATIONAL.read_text()) or {}
    sites = (nat.get("cmis_agendas") or {}).get("sites", [])
    if not sites:
        sys.exit("No cmis_agendas.sites in targets_national.yaml — "
                 "run discover_cmis_feeds.py first.")
    flipped = 0
    for site in sites:
        time.sleep(SLEEP_BETWEEN)
        try:
            html = _fetch(site)
        except Exception as e:
            site["note"] = f"meetings page failed: {e}"[:90]
            print(f"{site['name']:24} FAIL  {site['note']}")
            continue
        meetings = parse_calendar(html)
        has_module = ("MeetingCalendarPublic" in html or "rsApt" in html
                      or meetings)
        if has_module:
            site["status"] = "verified"
            site.pop("note", None)
            flipped += 1
            keys = sum(1 for m in meetings if _key(m["committee"]))
            print(f"{site['name']:24} OK    {len(meetings)} meetings this "
                  f"month ({keys} key) @ {meetings_url(site)}")
        else:
            site["note"] = "page loads but no calendar module"
            print(f"{site['name']:24} FAIL  {site['note']}")
    NATIONAL.write_text(yaml.safe_dump(nat, sort_keys=False,
                                       allow_unicode=True, width=1000))
    print(f"\n{flipped}/{len(sites)} sites verified and saved to "
          f"{NATIONAL.name}. (0 meetings can be normal — recess months.)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="live-check candidate sites and update the yaml")
    args = ap.parse_args()
    if args.verify:
        verify()
    else:
        conn = db.connect()
        items = scrape(conn)
        print(f"\n{len(items)} agenda items:")
        for it in items:
            print(f"- [{it['city']}] {it['title']}")
        conn.close()
