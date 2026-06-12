"""Verify every candidate subreddit: exists, public, and active (>=3 posts in 48h).
Updates config/targets.yaml in place (status: verified / dropped) and prints a
ranked activity report so you can see where the content actually is.

Run this BEFORE the first pipeline run, and re-run monthly:
  python scripts/verify_targets.py
"""
import sys
import time
from pathlib import Path

import praw
import prawcore
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from reddit_scraper import make_reddit  # noqa: E402

MIN_POSTS_48H = 3


def main():
    cfg_path = PROJECT_ROOT / "config" / "targets.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    reddit = make_reddit()
    cutoff = time.time() - 48 * 3600
    report = []
    for sub in cfg["subreddits"]:
        name = sub["name"]
        try:
            sr = reddit.subreddit(name)
            _ = sr.id  # forces fetch; raises if missing/banned/private
            if sr.subreddit_type not in ("public", "restricted"):
                sub["status"], note = "dropped", f"not public ({sr.subreddit_type})"
                recent = 0
            else:
                recent = sum(1 for p in sr.new(limit=50) if p.created_utc >= cutoff)
                if recent >= MIN_POSTS_48H:
                    sub["status"], note = "verified", f"{recent} posts/48h, {sr.subscribers:,} members"
                else:
                    sub["status"], note = "dropped", f"only {recent} posts/48h"
            report.append((name, sub["status"], recent, note))
        except (prawcore.exceptions.NotFound, prawcore.exceptions.Redirect):
            sub["status"] = "dropped"
            report.append((name, "dropped", 0, "does not exist"))
        except prawcore.exceptions.Forbidden:
            sub["status"] = "dropped"
            report.append((name, "dropped", 0, "private/banned"))
        except Exception as e:
            sub["status"] = "dropped"
            report.append((name, "dropped", 0, f"error: {e}"))
        time.sleep(1)

    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    report.sort(key=lambda r: r[2], reverse=True)
    print(f"\n{'subreddit':22} {'status':10} {'48h':>4}  note")
    print("-" * 70)
    for name, status, recent, note in report:
        print(f"r/{name:20} {status:10} {recent:>4}  {note}")
    verified = [r for r in report if r[1] == "verified"]
    print(f"\n{len(verified)} verified. config/targets.yaml updated — "
          f"the pipeline will use only these.")


if __name__ == "__main__":
    main()
