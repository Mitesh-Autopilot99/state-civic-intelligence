"""Discover CMIS committee sites for councils that have no ModernGov host.

ModernGov discovery (discover_council_feeds.py --national) found ~190 councils
with no guessable ModernGov host — many of the big ones (Birmingham, Belfast,
Brighton, Liverpool, Newcastle...) run "CMIS" committee software instead
(e.g. birmingham.cmis.uk.com). CMIS has no standard RSS, so this script only
does step 1: find and VERIFY each council's CMIS site, record the committee /
meetings-calendar pages that answer, and save a sample HTML page to
data/reference/cmis_samples/ so the HTML parser (cmis_source.py, step 2) can
be written against real markup rather than guesses.

Input:  data/reference/modgov_discovery.json (councils with host: null)
Output: config/targets_national.yaml  -> cmis_agendas.sites (status: candidate
        — NOT polled by the pipeline until the parser exists and verifies them)
        data/reference/cmis_discovery.json   (resumable progress, gitignored)
        data/reference/cmis_samples/*.html   (one sample page per found site)

Speed & politeness: same model as the ModernGov script — WORKERS councils in
parallel (each worker talks to a different server), 1 req/s within a council,
failed DNS guesses are free. Safe to Ctrl-C and re-run (resumes).

Run on the Mac:
    python scripts/discover_cmis_feeds.py            # full run, ~5-15 min
    python scripts/discover_cmis_feeds.py --limit 10 # test run
"""
from __future__ import annotations   # py3.9: allow `X | None` annotations

import argparse
import concurrent.futures
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NATIONAL = PROJECT_ROOT / "config" / "targets_national.yaml"
REF_COUNCILS = PROJECT_ROOT / "data" / "reference" / "uk_councils.csv"
MODGOV_PROGRESS = PROJECT_ROOT / "data" / "reference" / "modgov_discovery.json"
PROGRESS = PROJECT_ROOT / "data" / "reference" / "cmis_discovery.json"
SAMPLES = PROJECT_ROOT / "data" / "reference" / "cmis_samples"

UA = {"User-Agent": "Mozilla/5.0 (compatible; state-civic-listener/1.0; "
                    "feed discovery; contact thakermitesh89@gmail.com)"}
HOST_PROBE_TIMEOUT = 15
SLEEP = 1
WORKERS = 8

# host guesses, most common first; {s} = council slug
HOST_PATTERNS = ("https://{s}.cmis.uk.com",
                 "https://cmis.{s}.gov.uk")
# CMIS page names seen in the wild; probed relative to the site path
PAGE_CANDIDATES = ("Committee.aspx", "Committees.aspx", "MeetingCalendar.aspx",
                   "MeetingsCalendar.aspx", "Meetings.aspx",
                   "CalendarofMeetings.aspx")
# path segments to try when the root redirect doesn't reveal one
PATH_CANDIDATES = ("{slug}", "Live", "live", "cmis5", "ccc_live")


DEBUG = False


def get(url: str, timeout: int = HOST_PROBE_TIMEOUT) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r


def head_alive(url: str) -> requests.Response | None:
    """Is there a web server at all? ANY http status counts as alive — CMIS
    roots often 404/500 while the real site lives under /{path}/ (this is
    exactly how Birmingham was missed on the first run). None = dead host."""
    try:
        return requests.get(url, headers=UA, timeout=HOST_PROBE_TIMEOUT,
                            allow_redirects=True)
    except Exception:
        return None


def looks_cmis(html: str) -> bool:
    """A real CMIS committee/calendar page: ASP.NET page with committee or
    meeting links. Generic enough to survive version differences, strict
    enough to reject custom 404s / holding pages."""
    h = html.lower()
    if ".aspx" not in h:
        return False
    return ("committee" in h or "meeting" in h) and (
        "cmis" in h or "__viewstate" in h)


def _slug_variants(council: dict) -> list[str]:
    slug = (council.get("gov-uk-slug") or "").strip().lower()
    name = re.sub(r"[^a-z0-9-]+", "-",
                  council["nice-name"].strip().lower()).strip("-")
    out = []
    for s in (slug, slug.replace("-", ""), name, name.replace("-", "")):
        if s and s not in out:
            out.append(s)
    return out


def _probe_council(c: dict) -> tuple[str, dict]:
    """One council's unit of work (worker thread): guess CMIS hosts, derive
    the site path from the root redirect (or guess), verify committee/
    calendar pages, capture one sample HTML. Pure: no shared-state writes."""
    name = c["nice-name"].strip()
    result: dict = {"host": None, "pages": []}
    for s in _slug_variants(c):
        for hpat in HOST_PATTERNS:
            host = hpat.format(s=s)
            root = head_alive(host + "/")
            if root is None:
                if DEBUG:
                    print(f"    dead host: {host}")
                continue                      # DNS fail / timeout — next guess
            # a live root usually redirects to /{Site}/Default.aspx — learn the
            # path from it; an error status (404/500) is fine, host is alive
            seg = ""
            if root.ok:
                final = urlparse(root.url)
                seg = (final.path.strip("/").split("/")[0]
                       if final.path.strip("/") else "")
            if DEBUG:
                print(f"    live host: {host} (root {root.status_code}, "
                      f"path hint: {seg or '-'})")
            paths = []
            for p in ([seg] if seg else []) + [pc.format(slug=s)
                                               for pc in PATH_CANDIDATES]:
                if p and p not in paths:
                    paths.append(p)
            conn_fails = 0
            for path in paths:
                pages, sample = [], ""
                for page in PAGE_CANDIDATES:
                    time.sleep(SLEEP)
                    url = f"{host}/{path}/{page}"
                    try:
                        r = get(url)
                    except requests.exceptions.HTTPError as e:
                        if DEBUG:
                            print(f"    {url} -> {e.response.status_code}")
                        continue
                    except Exception as e:
                        conn_fails += 1
                        if DEBUG:
                            print(f"    {url} -> {type(e).__name__}")
                        if conn_fails >= 3 and not pages:
                            break             # wildcard-DNS ghost — stop early
                        continue
                    if looks_cmis(r.text):
                        pages.append(page)
                        if not sample:
                            sample = r.text
                    elif DEBUG:
                        print(f"    {url} -> 200 but not CMIS")
                if pages:
                    result.update({"host": host, "path": path, "pages": pages})
                    return name, result | {"_sample": sample}
                if conn_fails >= 3:
                    break                     # give up on this host
            # host resolved but no CMIS pages — note it, keep trying variants
            result["note"] = f"host answered but no CMIS pages: {host}"
    return name, result


def main(limit: int | None = None):
    if not MODGOV_PROGRESS.exists():
        sys.exit("Run discover_council_feeds.py --national first — this "
                 "script targets its misses.")
    if not REF_COUNCILS.exists():
        sys.exit("Reference data missing — run scripts/fetch_national_data.py.")
    modgov = json.loads(MODGOV_PROGRESS.read_text())
    misses = {n for n, v in modgov.items() if not v.get("host")}
    councils = [c for c in csv.DictReader(REF_COUNCILS.open())
                if c.get("current-authority") == "True"
                and c.get("lower-or-unitary") == "True"
                and c["nice-name"].strip() in misses]

    progress = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {}
    todo = [c for c in councils if c["nice-name"].strip() not in progress]
    if limit:
        todo = todo[:limit]
    print(f"CMIS discovery: {len(todo)} ModernGov-less councils to probe "
          f"({len(progress)} already decided — delete {PROGRESS.name} to redo).")
    if not todo:
        return

    nat = (yaml.safe_load(NATIONAL.read_text()) or {}) if NATIONAL.exists() else {}
    sites = nat.setdefault("cmis_agendas", {}).setdefault("sites", [])
    known = {s.get("base") for s in sites}

    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    found = 0
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)
    futures = [pool.submit(_probe_council, c) for c in todo]
    try:
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            name, result = fut.result()
            sample = result.pop("_sample", "")
            if result.get("host"):
                found += 1
                base = f"{result['host']}/{result['path']}"
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                if base not in known:
                    sites.append({"name": slug, "kind": "council_agenda_cmis",
                                  "label": name, "base": base,
                                  "pages": result["pages"],
                                  "status": "candidate"})
                    known.add(base)
                    NATIONAL.write_text(yaml.safe_dump(
                        nat, sort_keys=False, allow_unicode=True, width=1000))
                if sample:
                    (SAMPLES / f"{slug}.html").write_text(sample)
            progress[name] = result
            PROGRESS.write_text(json.dumps(progress, indent=1))
            tag = (f"CMIS @ {result['host']}/{result['path']} "
                   f"({', '.join(result['pages'])})" if result.get("host")
                   else result.get("note") or "no CMIS host found")
            print(f"[{i}/{len(todo)}] {name}: {tag}", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted — completed councils are saved; re-run to resume.")
        pool.shutdown(wait=False, cancel_futures=True)
        sys.exit(130)
    pool.shutdown()

    print(f"\nDone: {found} CMIS sites found (status: candidate — the HTML "
          f"parser comes next; they are NOT polled yet).")
    print(f"Sample pages saved to {SAMPLES.relative_to(PROJECT_ROOT)}/ — "
          f"share the folder/repo state so the parser can be written "
          f"against real markup.")


def debug_one(query: str):
    """Probe a single council verbosely (every URL + status), write nothing."""
    global DEBUG
    DEBUG = True
    rows = [c for c in csv.DictReader(REF_COUNCILS.open())
            if query.lower() in c["nice-name"].lower()]
    if not rows:
        sys.exit(f"No council matching {query!r} in reference data.")
    c = rows[0]
    print(f"Debug probe: {c['nice-name'].strip()} "
          f"(slugs: {', '.join(_slug_variants(c))})")
    name, result = _probe_council(c)
    result.pop("_sample", None)
    print(f"\nResult: {json.dumps(result, indent=1)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N councils (test runs)")
    ap.add_argument("--debug", metavar="COUNCIL", default=None,
                    help="probe one council verbosely, write nothing")
    args = ap.parse_args()
    if args.debug:
        debug_one(args.debug)
    else:
        main(args.limit)
