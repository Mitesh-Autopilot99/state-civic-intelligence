"""SQLite layer. GDPR rule enforced structurally: there are NO columns for
author names, handles, or raw post bodies. Only issue-level aggregates persist."""
import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("STATE_INTEL_DB", PROJECT_ROOT / "data" / "state_intel.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT UNIQUE,               -- dedupe key (category+area+summary fingerprint)
    category TEXT,                  -- potholes, bins, housing, transport, asb, planning, nhs, other
    area TEXT,                      -- free-text area the classifier extracted
    constituency TEXT,
    mp_name TEXT,
    summary TEXT,                   -- one-line ISSUE summary (not a quote of any post)
    urgency INTEGER,                -- 1-5
    specificity INTEGER,            -- 1-5
    volume INTEGER,                 -- number of posts contributing
    engagement INTEGER,             -- summed score+comments across contributing posts
    source_link TEXT,               -- ONE representative public permalink
    source_platform TEXT,
    trending INTEGER DEFAULT 0,
    suggested_action TEXT,          -- seed_motion / outreach / watch
    status TEXT DEFAULT 'new',      -- new / briefed / motion_seeded / dismissed
    first_seen TEXT,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS seen_posts (
    post_id TEXT PRIMARY KEY,       -- platform post id only; no author, no content
    seen_at TEXT
);
CREATE TABLE IF NOT EXISTS mp_cache (
    constituency TEXT PRIMARY KEY,
    mp_name TEXT,
    party TEXT,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS area_cache (
    area_text TEXT PRIMARY KEY,
    constituency TEXT,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS outreach_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT,                    -- group/RA name (organisational, not personal)
    constituency TEXT,
    issue_hash TEXT,
    drafted_at TEXT,
    sent_at TEXT,
    response TEXT,                  -- none / replied / shared / declined
    notes TEXT
);
CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT,
    source TEXT,
    posts_pulled INTEGER,
    posts_kept INTEGER,
    issues_written INTEGER,
    errors TEXT
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


if __name__ == "__main__":
    connect().close()
    print(f"Database ready at {DB_PATH}")
