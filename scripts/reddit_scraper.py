"""Reddit scraper. Read-only, OAuth, minimal footprint (~1 listing call per sub/day).
By design we NEVER read or store author fields. Post dicts live in memory for one
pipeline run only; raw text is discarded after classification.

Reddit free tier is non-commercial — keep volume minimal. Long-term fix: an
official commercial/civic-access conversation with Reddit (flagged in README).
"""
import logging
import os
import time
from pathlib import Path

import praw
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
log = logging.getLogger("reddit_scraper")

POSTS_PER_SUB = 100          # one .new() listing call per sub
MAX_AGE_HOURS = 24


def load_config():
    targets = yaml.safe_load((PROJECT_ROOT / "config" / "targets.yaml").read_text())
    kw = yaml.safe_load((PROJECT_ROOT / "config" / "keywords.yaml").read_text())
    subs = [s for s in targets["subreddits"] if s.get("status") == "verified"]
    return subs, [k.lower() for k in kw["keywords"]]


def make_reddit() -> praw.Reddit:
    return praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get("REDDIT_USER_AGENT", "state-civic-listener/1.0"),
        check_for_async=False,
    )


def matches_keywords(title: str, body: str, keywords: list[str]) -> bool:
    text = f"{title} {body}".lower()
    return any(k in text for k in keywords)


def scrape(conn) -> list[dict]:
    """Return keyword-matched posts from the last 24h that we haven't seen before.
    Each dict: id, subreddit, city, title, body, score, num_comments, permalink, created_utc.
    NO author field — intentionally never read."""
    subs, keywords = load_config()
    if not subs:
        log.warning("No verified subreddits in config/targets.yaml — run verify_targets.py first.")
        return []
    reddit = make_reddit()
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    seen = {r["post_id"] for r in conn.execute("SELECT post_id FROM seen_posts")}
    out = []
    for sub in subs:
        try:
            for post in reddit.subreddit(sub["name"]).new(limit=POSTS_PER_SUB):
                if post.created_utc < cutoff or post.id in seen:
                    continue
                body = (post.selftext or "")[:2000]
                if not matches_keywords(post.title, body, keywords):
                    continue
                out.append({
                    "id": post.id,
                    "subreddit": sub["name"],
                    "city": sub.get("city", sub["name"]),
                    "title": post.title,
                    "body": body,
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "permalink": f"https://www.reddit.com{post.permalink}",
                    "created_utc": post.created_utc,
                })
            time.sleep(1)  # polite pacing, far below rate limits
        except Exception as e:  # one bad sub must not kill the run
            log.error("r/%s failed: %s", sub["name"], e)
    log.info("Scraped %d keyword-matched new posts from %d subs", len(out), len(subs))
    return out


def mark_seen(conn, posts: list[dict]):
    conn.executemany(
        "INSERT OR IGNORE INTO seen_posts (post_id, seen_at) VALUES (?, datetime('now'))",
        [(p["id"],) for p in posts],
    )
    conn.commit()
