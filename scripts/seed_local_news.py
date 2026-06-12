"""Seed independent local-news feeds from the ICNN member directory.

ICNN (Independent Community News Network, ~120 member titles). The members
PAGE renders its list with JavaScript (the raw HTML has no outlet links —
verified live), but ICNN's "Interactive Map Of Members" is a Google My Maps
embed whose public KML export carries every member: name, a description that
usually contains the outlet's website, and map coordinates. This script:
  1. downloads that KML (one request) and extracts (name, site, lat/lon)
     per outlet,
  2. guesses the standard WordPress feed paths (/feed/, /feed, /rss) for each
     outlet site and keeps the FIRST one that answers with real RSS/Atom XML,
  3. labels each outlet with a council: first by matching name/domain against
     the reference council list (uk_councils.csv, nice-name + alt-names);
     failing that by REVERSE GEOCODING the map pin via postcodes.io
     (lat/lon -> admin_district). Outlets still unmatched are written
     status: needs_label and are NOT polled until a human labels them.
  4. appends new feeds (kind: local_news) to config/targets_national.yaml.
     URLs already present in either config are skipped, so the five
     hand-curated pilot feeds in targets.yaml are never touched, and re-runs
     are idempotent.

GDPR: outlet names and websites are organisations, not people; coordinates
are the outlet's public map pin. Nothing personal is fetched or stored.

Why this matters: this is the "power of the product" source — independent
local titles are what MPs actually read about their patch. Google News is the
baseline; ICNN members are the quality layer.

Run on the Mac (needs open internet), AFTER fetch_national_data.py:
    python scripts/seed_local_news.py
Allow ~10 minutes (~120 outlets, polite 1 req/s, up to 3 path guesses each).
"""
import csv
import html as html_mod
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PILOT = PROJECT_ROOT / "config" / "targets.yaml"
NATIONAL = PROJECT_ROOT / "config" / "targets_national.yaml"
REF_COUNCILS = PROJECT_ROOT / "data" / "reference" / "uk_councils.csv"

# Google My Maps embed on /interactive-map-of-members/ (mid verified live);
# forcekml=1 returns plain KML instead of a network-links wrapper.
KML_URL = ("https://www.google.com/maps/d/kml?"
           "mid=13EPEnZ3byJLcekzhu13Y2ZPCmGQzpEC1&forcekml=1")
POSTCODES_REVERSE = "https://api.postcodes.io/postcodes"
UA = {"User-Agent": "state-civic-listener/1.0 (local news feed discovery; "
                    "contact thakermitesh89@gmail.com)"}
TIMEOUT = 20
SLEEP = 1
FEED_PATHS = ("feed/", "feed", "rss")
# never outlets — directory chrome and social links
SKIP_DOMAINS = ("communityjournalism.co.uk", "cardiff.ac.uk", "twitter.com",
                "x.com", "facebook.com", "instagram.com", "linkedin.com",
                "youtube.com", "wordpress.org", "mailchi.mp", "eventbrite",
                "google.com", "bsky.app", "tiktok.com", "goo.gl")


def is_feed(text: str) -> bool:
    text = text.lstrip('\ufeff')
    if text.startswith("ï»¿"):
        text = text[3:]
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return False
    return root.tag.rsplit("}", 1)[-1].lower() in ("feed", "rss", "rdf")


def _site_from_description(desc: str) -> str:
    """First non-social http(s) URL in a placemark description, if any."""
    for url in re.findall(r'https?://[^\s"<>\\]+', desc or ""):
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if host and not any(s in host for s in SKIP_DOMAINS):
            return f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
    return ""


def outlets_from_kml() -> list[dict]:
    """[{name, site, lat, lon}] — one per ICNN member map placemark.
    site may be '' (no URL in the pin description); lat/lon may be None."""
    r = requests.get(KML_URL, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.text.lstrip('\ufeff'))
    out, seen = [], set()
    for pm in root.iter():
        if pm.tag.rsplit("}", 1)[-1] != "Placemark":
            continue
        name = desc = coords = ""
        for el in pm.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "name":
                name = html_mod.unescape((el.text or "").strip())
            elif tag == "description":
                desc = el.text or ""
            elif tag == "coordinates":
                coords = (el.text or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        lat = lon = None
        if coords:
            try:
                lon, lat = (float(x) for x in coords.split(",")[:2])
            except ValueError:
                pass
        out.append({"name": name, "site": _site_from_description(desc),
                    "lat": lat, "lon": lon})
    return out


def council_from_pin(lat, lon, index: list) -> str:
    """Reverse-geocode a map pin to a council via postcodes.io (free, no key).
    Returns '' when offshore/unknown — caller falls back to needs_label."""
    if lat is None or lon is None:
        return ""
    try:
        r = requests.get(POSTCODES_REVERSE,
                         params={"lat": lat, "lon": lon, "limit": 1,
                                 "radius": 2000}, headers=UA, timeout=TIMEOUT)
        results = (r.json() or {}).get("result") or []
        district = (results[0].get("admin_district") or "") if results else ""
    except Exception:
        return ""
    if not district:
        return ""
    key = _norm(district)
    for k, nice in index:
        if k == key or k in key or key in k:
            return nice
    return ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def council_index() -> list[tuple[str, str]]:
    """[(normalised_key, nice-name)] longest keys first — greedy match wins."""
    idx = {}
    for c in csv.DictReader(REF_COUNCILS.open()):
        if c.get("current-authority") != "True":
            continue
        nice = c["nice-name"].strip()
        names = [nice] + [a for a in re.split(r"[|;,]",
                                              c.get("alt-names") or "") if a.strip()]
        for n in names:
            k = _norm(n)
            if len(k) >= 4:
                idx.setdefault(k, nice)
    return sorted(idx.items(), key=lambda kv: -len(kv[0]))


def match_council(outlet: dict, index: list) -> str:
    hay = _norm(outlet["name"]) + " " + _norm(urlparse(outlet["site"]).netloc)
    for key, nice in index:
        if key in hay:
            return nice
    return ""


def find_feed(site: str) -> str:
    for path in FEED_PATHS:
        url = urljoin(site, path)
        time.sleep(SLEEP)
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            if r.status_code == 200 and is_feed(r.text):
                return url
        except Exception:
            pass
    return ""


def main():
    if not REF_COUNCILS.exists():
        sys.exit("Reference data missing — run scripts/fetch_national_data.py first.")
    index = council_index()

    nat = (yaml.safe_load(NATIONAL.read_text()) or {}) if NATIONAL.exists() else {}
    nat.setdefault("council_news", {}).setdefault("feeds", [])
    pilot = yaml.safe_load(PILOT.read_text()) or {}
    existing = {f.get("url") for f in nat["council_news"]["feeds"]}
    existing |= {f.get("url") for f in (pilot.get("council_news") or {}).get("feeds", [])}
    existing_hosts = {urlparse(u).netloc.removeprefix("www.")
                      for u in existing if u}

    try:
        outlets = outlets_from_kml()
    except Exception as e:
        sys.exit(f"ICNN member map (KML) unreachable: {e}")
    print(f"ICNN member map: {len(outlets)} outlets.")

    added, needs_label, dead, no_site = [], 0, 0, []
    for i, o in enumerate(outlets, 1):
        if not o["site"]:
            no_site.append(o["name"])
            print(f"[{i}/{len(outlets)}] {o['name']}: no website in map pin")
            continue
        host = urlparse(o["site"]).netloc.removeprefix("www.")
        if host in existing_hosts:
            continue
        feed = find_feed(o["site"])
        if not feed:
            dead += 1
            print(f"[{i}/{len(outlets)}] {o['name']}: no feed found at {o['site']}")
            continue
        label = match_council(o, index)
        via = "name match"
        if not label:
            time.sleep(SLEEP)
            label = council_from_pin(o["lat"], o["lon"], index)
            via = "map pin"
        slug = re.sub(r"[^a-z0-9]+", "-", o["name"].lower()).strip("-")[:40]
        entry = {"name": f"icnn-{slug}", "kind": "local_news",
                 "label": label or o["name"],
                 "url": feed,
                 "status": "verified" if label else "needs_label"}
        if not label:
            needs_label += 1
        nat["council_news"]["feeds"].append(entry)
        existing.add(feed)
        existing_hosts.add(host)
        added.append(entry)
        print(f"[{i}/{len(outlets)}] {o['name']}: {feed} "
              f"-> {label + ' (' + via + ')' if label else 'NEEDS LABEL'}")
        # save as we go — safe to interrupt and re-run
        NATIONAL.write_text(yaml.safe_dump(nat, sort_keys=False,
                                           allow_unicode=True, width=1000))

    print(f"\n{len(added)} feeds written to {NATIONAL.name} "
          f"({len(added) - needs_label} labelled, {needs_label} need a label, "
          f"{dead} outlets without a discoverable feed, "
          f"{len(no_site)} pins without a website).")
    if no_site:
        print("Pins without a website (add by hand if wanted): "
              + ", ".join(no_site[:20]) + ("..." if len(no_site) > 20 else ""))
    if needs_label:
        print("Feeds with status: needs_label are NOT polled. Edit "
              "config/targets_national.yaml: set the right council label and "
              "change status to verified.")


if __name__ == "__main__":
    main()
