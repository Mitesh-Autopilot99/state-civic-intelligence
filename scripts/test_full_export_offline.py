from __future__ import annotations
"""Offline tests for the full per-council export path (scorer full_sink).
No network, no API key, no real DB file required.

Run: python scripts/test_full_export_offline.py
Exits non-zero on any failure.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scorer  # noqa: E402
import config_loader  # noqa: E402

PASS = 0
FAIL = 0

# columns scorer reads (hash/summary/constituency) and writes on persist
_ISSUES_DDL = """
CREATE TABLE issues (
    hash TEXT PRIMARY KEY, category TEXT, area TEXT, constituency TEXT,
    mp_name TEXT, summary TEXT, urgency INT, specificity INT, volume INT,
    engagement REAL, source_link TEXT, source_platform TEXT, trending INT,
    suggested_action TEXT, status TEXT, first_seen TEXT, last_seen TEXT
)
"""

_CLEAN_KEYS = {"category", "area", "constituency", "mp_name", "summary",
               "score", "trending", "suggested_action", "source_link",
               "source_type", "volume", "engagement"}


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_ISSUES_DDL)
    return c


def _items(n):
    """n distinct civic items across n councils (no near-duplicates -> no merge)."""
    return [{"category": "housing", "area": f"Council{i}", "city": f"Council{i}",
             "constituency": f"Con{i}", "mp_name": f"MP{i}",
             "summary": f"Distinct civic issue number {i} on a named street",
             "urgency": 3, "specificity": 3, "score": i, "num_comments": 0,
             "permalink": f"https://example.org/{i}", "platform": "reddit",
             "source_type": "reddit"} for i in range(n)]


def _top_n():
    try:
        return int((config_loader.load_targets().get("limits") or {}).get("top_n") or scorer.TOP_N)
    except Exception:
        return scorer.TOP_N


def test_full_sink_captures_all():
    print("test_full_sink_captures_all")
    n = _top_n() + 20            # comfortably more items than the digest cap
    full = []
    top = scorer.group_and_score(_conn(), _items(n), full_sink=full)
    check(len(top) == _top_n(), f"return sliced to top_n ({len(top)} vs {_top_n()})")
    check(len(full) == n, f"full_sink holds every issue ({len(full)} vs {n})")
    check(len(full) > len(top), "full set is larger than the digest")


def test_full_sink_clean_and_serialisable():
    print("test_full_sink_clean_and_serialisable")
    full = []
    scorer.group_and_score(_conn(), _items(30), full_sink=full)
    check(_CLEAN_KEYS.issubset(full[0].keys()), "each issue has the clean site shape")
    try:
        json.dumps(full)
        check(True, "full_sink is JSON-serialisable")
    except (TypeError, ValueError) as e:
        check(False, f"full_sink not JSON-serialisable: {e}")


def test_top_is_sorted_prefix():
    print("test_top_is_sorted_prefix")
    full = []
    top = scorer.group_and_score(_conn(), _items(_top_n() + 10), full_sink=full)
    check([t["summary"] for t in top] == [f["summary"] for f in full[:len(top)]],
          "digest is the score-sorted prefix of the full set")
    scores = [f["score"] for f in full]
    check(scores == sorted(scores, reverse=True), "full set is score-descending")


def test_without_full_sink_unchanged():
    print("test_without_full_sink_unchanged")
    # Legacy call shape (no full_sink) must still work and return top_n.
    top = scorer.group_and_score(_conn(), _items(_top_n() + 5))
    check(len(top) == _top_n(), "no-sink call still returns top_n digest")


def test_empty_items():
    print("test_empty_items")
    full = []
    top = scorer.group_and_score(_conn(), [], full_sink=full)
    check(top == [], "empty input -> empty digest")
    check(full == [], "empty input -> empty full set")


def main():
    print("=== full-export offline tests ===")
    test_full_sink_captures_all()
    test_full_sink_clean_and_serialisable()
    test_top_is_sorted_prefix()
    test_without_full_sink_unchanged()
    test_empty_items()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
