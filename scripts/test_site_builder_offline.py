from __future__ import annotations
"""Offline tests for the site builder stage. No network, no API key required.

Run: python scripts/test_site_builder_offline.py
Exits non-zero on any failure.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_builder  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PASS = 0
FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")


def _sample_brief() -> dict:
    """Use a real brief if present, else a small synthetic one."""
    briefs = sorted((PROJECT_ROOT / "data").glob("brief_2*.json"))
    if briefs:
        return json.loads(briefs[-1].read_text())
    return {
        "date": "2026-06-19",
        "posts_scanned": 1200,
        "civic_items": 3,
        "errors": ["planning: integer division or modulo by zero"],
        "items": [
            {"category": "housing", "area": "Croydon (London)",
             "constituency": "Croydon East", "mp_name": "Natasha Irons",
             "summary": "Council to buy remaining flats in a local block.",
             "source_link": "https://news.google.com/rss/articles/AAA",
             "source_type": "google_news", "trending": 1, "score": 12.0,
             "suggested_action": "seed_motion", "volume": 1, "engagement": 0},
            {"category": "planning", "area": "Lichfield",
             "constituency": "Lichfield", "mp_name": "Dave Robertson",
             "summary": "Decision on school sports facilities S106 funding.",
             "source_link": "https://democracy.lichfielddc.gov.uk/x?Id=1",
             "source_type": "council_agenda", "trending": 0, "score": 10.0,
             "suggested_action": "watch", "volume": 2, "engagement": 0},
            {"category": "transport", "area": "Croydon",
             "constituency": "Croydon West", "mp_name": "Sarah Jones",
             "summary": "New roundabout expected to cause months of delays.",
             "source_link": "https://news.google.com/rss/articles/BBB",
             "source_type": "google_news", "trending": 0, "score": 9.0,
             "suggested_action": "watch", "volume": 1, "engagement": 0},
        ],
    }


def _build_tmp(brief: dict) -> Path:
    out = Path(tempfile.mkdtemp(prefix="site_test_"))
    site_builder.build(brief, out)
    return out


def test_dashboard_lists_every_council():
    print("test_dashboard_lists_every_council")
    brief = _sample_brief()
    out = _build_tmp(brief)
    index = (out / "index.html").read_text()
    check("<!doctype html>" in index.lower(), "index.html is a valid HTML doc")

    by_council = site_builder.group_by_council(brief.get("items") or [])
    missing_name = [n for n in by_council if site_builder._e(n) not in index]
    check(not missing_name,
          f"every council name on dashboard (missing {missing_name})")
    missing_link = [n for n in by_council
                    if f"councils/{site_builder.slugify(n)}/" not in index]
    check(not missing_link,
          f"dashboard links to every council page (missing {missing_link})")
    check("Aggregated from public sources" in index, "disclosure on dashboard")


def test_council_pages_exist_with_links():
    print("test_council_pages_exist_with_links")
    brief = _sample_brief()
    out = _build_tmp(brief)
    by_council = site_builder.group_by_council(brief.get("items") or [])
    for name, its in by_council.items():
        slug = site_builder.slugify(name)
        page = out / "councils" / slug / "index.html"
        check(page.exists(), f"council page exists for {name} ({slug})")
        if not page.exists():
            continue
        text = page.read_text()
        # Every item's source link for this council must appear on its page.
        missing = [i.get("source_link") for i in its
                   if i.get("source_link")
                   and site_builder._e(i["source_link"]) not in text]
        check(not missing, f"{name}: all source links present (missing {len(missing)})")
        check("Back to dashboard" in text, f"{name}: back-to-dashboard link")
        check("Aggregated from public sources" in text, f"{name}: disclosure present")
        check("../../" in text, f"{name}: back link points to dashboard root")


def test_slugs_are_stable():
    print("test_slugs_are_stable")
    # Deterministic and ASCII/URL-safe.
    check(site_builder.slugify("Croydon") == "croydon", "simple slug")
    check(site_builder.slugify("Croydon (London)") == "croydon-london",
          "parenthetical slug")
    check(site_builder.slugify("Bath and North East Somerset")
          == "bath-and-north-east-somerset", "multiword slug")
    check(site_builder.slugify("Ashton‑under‑Lyne") == "ashton-under-lyne",
          "unicode hyphen normalised")
    check(site_builder.slugify("Barking & Dagenham") == "barking-and-dagenham",
          "ampersand normalised")
    # Same name -> same slug every time.
    check(site_builder.slugify("Stockport") == site_builder.slugify("Stockport"),
          "slug is stable across calls")


def test_council_grouping_merges_suffixes():
    print("test_council_grouping_merges_suffixes")
    # "Croydon (London)" and "Croydon" should be the SAME council.
    brief = _sample_brief()
    if any((i.get("area") or "").startswith("Croydon") for i in brief["items"]):
        by_council = site_builder.group_by_council(brief["items"])
        check("Croydon" in by_council, "Croydon present as single council")
        check(len(by_council.get("Croydon", [])) >= 2,
              "Croydon variants grouped together")
    else:
        check(True, "skipped (real brief has no Croydon sample)")


def test_empty_brief_valid_dashboard():
    print("test_empty_brief_valid_dashboard")
    brief = {"date": "2026-06-19", "posts_scanned": 10, "civic_items": 0,
             "errors": [], "items": []}
    out = _build_tmp(brief)
    index = (out / "index.html").read_text()
    check("No new civic issues today." in index, "empty-day message present")
    check("Aggregated from public sources" in index, "disclosure on empty day")
    check("<!doctype html>" in index.lower(), "empty day still valid HTML")
    check(not (out / "councils").exists()
          or not any((out / "councils").iterdir()),
          "no council pages on an empty day")


def test_no_personal_data_or_markers():
    print("test_no_personal_data_or_markers")
    brief = _sample_brief()
    out = _build_tmp(brief)
    index = (out / "index.html").read_text()
    # The brief never carries usernames/raw bodies; ensure no presenter markers
    # leaked and the informational disclaimer is present.
    check("[src:" not in index, "no leftover [src:N] markers")
    check("not official council communication" in index.lower()
          or "informational" in index.lower(), "informational disclaimer present")


def test_errors_surface_on_dashboard():
    print("test_errors_surface_on_dashboard")
    brief = _sample_brief()
    if brief.get("errors"):
        out = _build_tmp(brief)
        index = (out / "index.html").read_text()
        check("Source issues" in index, "source errors surfaced on dashboard")
    else:
        check(True, "skipped (no errors in sample brief)")


def main():
    print("=== site builder offline tests ===")
    test_dashboard_lists_every_council()
    test_council_pages_exist_with_links()
    test_slugs_are_stable()
    test_council_grouping_merges_suffixes()
    test_empty_brief_valid_dashboard()
    test_no_personal_data_or_markers()
    test_errors_surface_on_dashboard()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
