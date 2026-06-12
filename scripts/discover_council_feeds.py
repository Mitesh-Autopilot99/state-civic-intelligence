"""Discover REAL per-committee ModernGov RSS feeds and write whatever verifies
into config (kind: council_agenda).

Pilot mode (default): the five London boroughs -> config/targets.yaml.
Lewisham publishes an index of ~44 per-committee feeds; we learn the URL
pattern from that page, then probe the same pattern against each other host's
committee list. Nothing is assumed — only feeds that return real RSS/Atom XML
are written, everything else is reported as dead on screen.

National mode (--national): every lower-tier/unitary authority from the
reference CSV -> config/targets_national.yaml. There is NO central directory
of ModernGov hosts (researched), so for each council we GUESS standard host
patterns (democracy.{slug}.gov.uk, {slug}.moderngov.co.uk, ...), confirm by
fetching mgListCommittees.aspx, then probe the standard RSS patterns and
verify each key-committee feed live before writing it. Progress is saved to
data/reference/modgov_discovery.json after every council, so the run is safe
to interrupt and resume (already-decided councils are skipped). Politeness is
per-host: failed DNS guesses cost nothing; real hosts get 1 req/s.

Run on the Mac (needs open internet):
    python scripts/discover_council_feeds.py                # pilot, once
    python scripts/discover_council_feeds.py --national     # allow 1-2 hours
    python scripts/discover_council_feeds.py --national --limit 10   # test run
"""
from __future__ import annotations   # py3.9: allow `list | None` annotations

import argparse
import csv
import html as html_mod
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / "config" / "targets.yaml"
NATIONAL = PROJECT_ROOT / "config" / "targets_national.yaml"
REF_COUNCILS = PROJECT_ROOT / "data" / "reference" / "uk_councils.csv"
PROGRESS = PROJECT_ROOT / "data" / "reference" / "modgov_discovery.json"
# Mozilla-compatible FORMAT (lewisham.gov.uk's CMS 404s unknown UAs) but still
# fully disclosed: project name + contact stay in the string.
UA = {"User-Agent": "Mozilla/5.0 (compatible; state-civic-listener/1.0; "
                    "feed discovery; contact thakermitesh89@gmail.com)"}
TIMEOUT = 30
SLEEP = 1

LEWISHAM_INDEX = "https://lewisham.gov.uk/about-this-site/rss-feeds"
LEWISHAM_MODGOV = "https://councilmeetings.lewisham.gov.uk"
# fallback patterns to PROBE (never assumed — only a live RSS response counts)
CANDIDATE_PATTERNS = ("Type=2&CId={cid}", "Type=4&CId={cid}",
                      "Type=1&CId={cid}", "Type=3&CId={cid}")

# the committees worth a daily poll — match on committee name, lowercase
KEY_COMMITTEES = ("planning", "cabinet", "full council", "council ", "scrutiny",
                  "overview", "mayor")
# ...minus ceremonial/defunct/duplicative ones (matched the keywords above on
# the first live run; pruned by hand — keep this list in sync with targets.yaml)
NOISE = ("religious", "urgency", "mayoralty", "honorary", "tax setting",
         "pre-application", "child q", "procurement", "call-in", "(call")
MAX_PER_HOST = 6

HOSTS = {  # ModernGov hosts to probe with the learned pattern
    "Croydon (London)": "https://democracy.croydon.gov.uk",
    "Hackney (London)": "https://hackney.moderngov.co.uk",
    "Waltham Forest (London)": "https://democracy.walthamforest.gov.uk",
    "Ealing (London)": "https://ealing.moderngov.co.uk",
}
# Ealing also runs ealing.cmis.uk.com — CMIS has no standard RSS, so Ealing
# coverage comes from Google News + ealing.news if the ModernGov probe fails.


def get(url: str, timeout: int = TIMEOUT) -> str:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text


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


def _key(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in KEY_COMMITTEES) and not any(x in n for x in NOISE)


def _slug(label: str, name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    borough = label.split(" (")[0].lower().replace(" ", "")
    return f"{borough}-{s}"


def learn_lewisham() -> tuple[list[dict], str]:
    """Returns (lewisham feeds to add, query template with {cid} placeholder)."""
    html = get(LEWISHAM_INDEX)
    # anchors whose href contains mgRss.aspx; capture href + link text
    pairs = re.findall(r'<a[^>]+href="([^"]*mgRss\.aspx[^"]*)"[^>]*>(.*?)</a>',
                       html, re.I | re.S)
    feeds, template = [], ""
    for href, text in pairs:
        url = urljoin(LEWISHAM_INDEX, href.replace("&amp;", "&"))
        name = html_mod.unescape(re.sub(r"<[^>]+>|\s+", " ", text)).strip()
        if not template:
            m = re.search(r"mgRss\.aspx\?(.*)", url, re.I)
            if m and re.search(r"\d+", m.group(1)):
                qs = m.group(1)
                # the committee id lives in CId/CommitteeId/ID — never in Type=
                t = re.sub(r"((?:CId|CommitteeId|ID)=)\d+", r"\g<1>{cid}",
                           qs, count=1, flags=re.I)
                template = t if "{cid}" in t else re.sub(r"(\d+)(?!.*\d)", "{cid}", qs)
        if _key(name):
            feeds.append({"name": _slug("Lewisham (London)", name),
                          "kind": "council_agenda", "label": "Lewisham (London)",
                          "url": url, "status": "candidate", "committee": name})
    return feeds[:MAX_PER_HOST], template


def _parse_committees(html: str, key_only: bool = True) -> list[tuple[str, str]]:
    """[(committee_id, committee_name)] from a ModernGov committee-list page."""
    pairs = re.findall(
        r'<a[^>]+href="[^"]*(?:CommitteeId|ID|CId)=(\d+)[^"]*"[^>]*>(.*?)</a>',
        html, re.I | re.S)
    out, seen = [], set()
    for cid, text in pairs:
        name = html_mod.unescape(re.sub(r"<[^>]+>|\s+", " ", text)).strip()
        if name and cid not in seen and (not key_only or _key(name)):
            seen.add(cid)
            out.append((cid, name))
    return out


def committees_for(base: str) -> list[tuple[str, str]]:
    return _parse_committees(get(f"{base}/mgListCommittees.aspx?bcr=1"))


def probe_template(base: str, comms: list | None = None) -> str:
    """Try each standard ModernGov RSS pattern against one real committee on
    `base`; return the first pattern that answers with genuine RSS/Atom."""
    if comms is None:
        try:
            comms = committees_for(base)
        except Exception:
            return ""
    if not comms:
        return ""
    cid = comms[0][0]
    for pat in CANDIDATE_PATTERNS:
        time.sleep(SLEEP)
        try:
            if is_feed(get(f"{base}/mgRss.aspx?{pat.format(cid=cid)}")):
                return pat
        except Exception:
            pass
    return ""


# --- national mode -----------------------------------------------------------
# host-pattern guesses, most common first; {s} = gov-uk slug (with and without
# hyphens — e.g. waltham-forest publishes at democracy.walthamforest.gov.uk)
HOST_PATTERNS = ("https://democracy.{s}.gov.uk",
                 "https://{s}.moderngov.co.uk",
                 "https://moderngov.{s}.gov.uk",
                 "https://committees.{s}.gov.uk")
HOST_PROBE_TIMEOUT = 15


def _slug_variants(council: dict) -> list[str]:
    slug = (council.get("gov-uk-slug") or "").strip().lower()
    name = re.sub(r"[^a-z0-9-]+", "-", council["nice-name"].strip().lower()).strip("-")
    out = []
    for s in (slug, slug.replace("-", ""), name, name.replace("-", "")):
        if s and s not in out:
            out.append(s)
    return out


def _find_host(council: dict, existing_hosts: set) -> tuple[str, list]:
    """Guess host patterns; return (base, all_committees) for the first host
    whose committee list answers. ('', []) if none. Failed DNS guesses are
    free; only real hosts incur the politeness sleep (handled by caller)."""
    for s in _slug_variants(council):
        for pat in HOST_PATTERNS:
            base = pat.format(s=s)
            if urlparse(base).netloc in existing_hosts:
                return base, []          # already covered by pilot/previous run
            try:
                html = get(f"{base}/mgListCommittees.aspx?bcr=1",
                           timeout=HOST_PROBE_TIMEOUT)
            except Exception:
                continue                 # DNS fail / 404 / timeout — next guess
            comms = _parse_committees(html, key_only=False)
            if comms:
                return base, comms
    return "", []


def main_national(limit: int | None = None):
    if not REF_COUNCILS.exists():
        sys.exit("Reference data missing — run scripts/fetch_national_data.py first.")
    councils = [c for c in csv.DictReader(REF_COUNCILS.open())
                if c.get("current-authority") == "True"
                and c.get("lower-or-unitary") == "True"]

    nat = (yaml.safe_load(NATIONAL.read_text()) or {}) if NATIONAL.exists() else {}
    nat.setdefault("council_news", {}).setdefault("feeds", [])
    pilot = yaml.safe_load(CONFIG.read_text()) or {}
    existing_urls = {f.get("url") for f in nat["council_news"]["feeds"]}
    existing_urls |= {f.get("url") for f in
                      (pilot.get("council_news") or {}).get("feeds", [])}
    existing_hosts = {urlparse(u).netloc for u in existing_urls
                      if u and "mgRss" in u}

    progress = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {}
    todo = [c for c in councils if c["nice-name"].strip() not in progress]
    if limit:
        todo = todo[:limit]
    print(f"ModernGov national discovery: {len(todo)} councils to probe "
          f"({len(progress)} already decided — delete {PROGRESS.name} to redo).")

    added_total = found_hosts = 0
    for i, c in enumerate(todo, 1):
        name = c["nice-name"].strip()
        base, comms = _find_host(c, existing_hosts)
        result = {"host": base or None, "feeds": 0}
        if base and not comms:
            result["note"] = "already covered"
        elif base:
            found_hosts += 1
            time.sleep(SLEEP)
            key_comms = [(cid, n) for cid, n in comms if _key(n)][:MAX_PER_HOST]
            template = probe_template(base, comms=key_comms or comms)
            for cid, cname in (key_comms if template else []):
                url = f"{base}/mgRss.aspx?{template.format(cid=cid)}"
                if url in existing_urls:
                    continue
                time.sleep(SLEEP)
                try:
                    ok = is_feed(get(url))
                except Exception:
                    ok = False
                if ok:
                    nat["council_news"]["feeds"].append(
                        {"name": _slug(name, cname), "kind": "council_agenda",
                         "label": name, "url": url, "status": "verified"})
                    existing_urls.add(url)
                    result["feeds"] += 1
            existing_hosts.add(urlparse(base).netloc)
            added_total += result["feeds"]
        progress[name] = result
        PROGRESS.parent.mkdir(parents=True, exist_ok=True)
        PROGRESS.write_text(json.dumps(progress, indent=1))
        if result["feeds"]:
            NATIONAL.write_text(yaml.safe_dump(nat, sort_keys=False,
                                               allow_unicode=True, width=1000))
        tag = (f"{result['feeds']} feeds @ {base}" if result["feeds"]
               else result.get("note") or base or "no ModernGov host found")
        print(f"[{i}/{len(todo)}] {name}: {tag}")

    print(f"\nDone: {found_hosts} new ModernGov hosts, {added_total} verified "
          f"agenda feeds written to {NATIONAL.name}.")
    hostless = sum(1 for v in progress.values() if not v.get("host"))
    print(f"{hostless} councils have no guessable ModernGov host — they're "
          f"covered by Google News + FixMyStreet + PlanIt instead.")


def main():
    cfg = yaml.safe_load(CONFIG.read_text())
    existing = {f["url"] for f in (cfg.get("council_news") or {}).get("feeds", [])}
    added, report = [], []

    # 1. Lewisham — published index preferred, live-probe fallback. No guessing:
    #    either way, only URLs that answer with real RSS get written.
    lew_feeds, template = [], ""
    try:
        lew_feeds, template = learn_lewisham()
        print(f"Lewisham index: {len(lew_feeds)} key-committee feeds; "
              f"learned pattern: mgRss.aspx?{template or 'NOT LEARNED'}")
    except Exception as e:
        print(f"Lewisham index unreachable ({e}).")
    if not template:
        print("Falling back: probing standard ModernGov RSS patterns against "
              f"{LEWISHAM_MODGOV} (Lewisham is known to publish per-committee RSS).")
        template = probe_template(LEWISHAM_MODGOV)
        if not template:
            print("FATAL: no pattern verified by live probe either. Nothing written.")
            sys.exit(1)
        print(f"Pattern verified by live probe: mgRss.aspx?{template}")

    hosts = dict(HOSTS)
    if not lew_feeds:
        # index gave us nothing — treat Lewisham like any other host
        hosts["Lewisham (London)"] = LEWISHAM_MODGOV

    for f in lew_feeds:
        time.sleep(SLEEP)
        try:
            ok = is_feed(get(f["url"]))
        except Exception:
            ok = False
        f["status"] = "verified" if ok else "dead"
        report.append((f["label"], f.pop("committee"), f["url"], f["status"]))
        if ok and f["url"] not in existing:
            added.append(f)

    # 2. remaining hosts — probe the verified pattern per key committee
    for label, base in hosts.items():
        try:
            comms = committees_for(base)
        except Exception as e:
            report.append((label, "(committee list)", base, f"dead ({e})"[:60]))
            continue
        for cid, name in comms[:MAX_PER_HOST]:
            url = f"{base}/mgRss.aspx?{template.format(cid=cid)}"
            time.sleep(SLEEP)
            try:
                ok = is_feed(get(url))
            except Exception:
                ok = False
            status = "verified" if ok else "dead"
            report.append((label, name, url, status))
            if ok and url not in existing:
                added.append({"name": _slug(label, name), "kind": "council_agenda",
                              "label": label, "url": url, "status": "verified"})

    print(f"\n{'council':<26} {'committee':<38} status")
    for label, name, url, status in report:
        print(f"{label:<26} {name[:36]:<38} {status}")
        print(f"{'':26} {url}")

    if added:
        cfg.setdefault("council_news", {}).setdefault("feeds", []).extend(added)
        CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
        print(f"\n{len(added)} verified feeds written to config/targets.yaml.")
    else:
        print("\nNo new feeds verified — config unchanged. "
              "(Ealing note: ealing.cmis.uk.com has no standard RSS; "
              "Ealing coverage = Google News + ealing.news.)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--national", action="store_true",
                    help="probe every lower-tier/unitary authority "
                         "-> targets_national.yaml (resumable)")
    ap.add_argument("--limit", type=int, default=None,
                    help="national mode: stop after N councils (test runs)")
    args = ap.parse_args()
    if args.national:
        main_national(args.limit)
    else:
        main()
