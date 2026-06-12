"""Facebook public-groups scraper via Apify (apify/facebook-groups-scraper).

Free-tier frugal: one actor run per day covering all verified groups, with a
hard daily post budget so a month stays inside Apify's $5/month free credit
(actor cost ~ $2.60-$5 per 1,000 posts).

Compliance (same as Reddit):
- Public groups only. The actor cannot access private groups (no login).
- We NEVER read or store author fields: the actor's 'user', 'topComments',
  profile/media fields are ignored entirely.
- Raw text lives in memory for one pipeline run; after classification only
  issue-level aggregates are kept.
"""
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
log = logging.getLogger("facebook_scraper")

ACTOR = "apify~facebook-groups-scraper"
RUN_SYNC_URL = "https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items" % ACTOR
DAILY_POST_BUDGET = 30       # ~900 posts/month -> ≤ ~$4.50, inside the free $5 credit
MIN_PER_GROUP = 3
MAX_AGE_HOURS = 24
HTTP_TIMEOUT = 330           # actor sync run waits up to 300s server-side


def load_config():
    targets = yaml.safe_load((PROJECT_ROOT / "config" / "targets.yaml").read_text())
    kw = yaml.safe_load((PROJECT_ROOT / "config" / "keywords.yaml").read_text())
    groups = [g for g in (targets.get("facebook_groups") or [])
              if g.get("status") == "verified"]
    return groups, [k.lower() for k in kw["keywords"]]


def _matches(text: str, keywords: list) -> bool:
    t = text.lower()
    return any(k in t for k in keywords)


def _epoch(iso: str) -> float:
    """'2026-06-11T04:37:15.000Z' -> unix epoch. Returns 0.0 if unparseable."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return 0.0


def scrape(conn) -> list:
    """Return keyword-matched public-group posts from the last 24h, unseen before.
    Same dict shape as reddit_scraper.scrape(); NO author fields, by design."""
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        log.info("APIFY_TOKEN not set — Facebook source disabled, skipping.")
        return []
    groups, keywords = load_config()
    if not groups:
        log.warning("No verified facebook_groups in config/targets.yaml — skipping.")
        return []

    per_group = max(MIN_PER_GROUP, DAILY_POST_BUDGET // len(groups))
    run_input = {
        "startUrls": [{"url": g["url"]} for g in groups],
        "resultsLimit": per_group,
    }
    try:
        r = requests.post(RUN_SYNC_URL, params={"token": token, "timeout": 300},
                          json=run_input, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        raw_items = r.json()
    except Exception as e:
        log.error("Apify actor run failed: %s", e)
        return []
    if not isinstance(raw_items, list):
        log.error("Unexpected Apify response type: %s", type(raw_items).__name__)
        return []

    by_url = {g["url"].rstrip("/"): g for g in groups}
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    seen = {row["post_id"] for row in conn.execute("SELECT post_id FROM seen_posts")}
    out = []
    for it in raw_items:
        # GDPR: read ONLY these fields. 'user', 'topComments', media/profile
        # fields are never touched.
        text = (it.get("text") or "").strip()
        url = it.get("url") or ""
        post_id = str(it.get("legacyId") or it.get("id") or url)
        created = _epoch(it.get("time", ""))
        if not text or not url or not post_id:
            continue
        if created < cutoff or post_id in seen:
            continue
        if not _matches(text, keywords):
            continue
        group = by_url.get((it.get("inputUrl") or "").rstrip("/"), {})
        label = group.get("name") or it.get("groupTitle") or "facebook-group"
        title = text.splitlines()[0][:120] if text else ""
        out.append({
            "id": post_id,
            "subreddit": label,                 # group label; keeps classifier shape
            "city": group.get("city", label),
            "title": title,
            "body": text[:2000],
            "score": int(it.get("likesCount") or 0),
            "num_comments": int(it.get("commentsCount") or 0),
            "permalink": url,
            "created_utc": created,
            "platform": "facebook",
        })
    log.info("Facebook: %d raw -> %d keyword-matched new posts from %d groups",
             len(raw_items), len(out), len(groups))
    return out
