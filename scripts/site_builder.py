from __future__ import annotations
"""Site builder stage — turn the daily scored brief into a static website.

This runs AFTER the presenter, as the final pipeline stage. It is PURE and
DETERMINISTIC: no network, no LLM, no database. It reads the in-memory brief
dict (the same structure written to data/brief_<date>.json) and writes a small
static site into out_dir (default: PROJECT_ROOT/site):

    site/index.html                     dashboard — every council with items today
    site/councils/<slug>/index.html     one page per council, full detail

The dashboard is the hub; council pages are the spokes. Each council card on the
dashboard links to that council's page; each council page links back.

The HTML is self-contained (inline CSS, no build step, no external assets) so
Netlify can serve it as-is. Styling follows the State brand palette (dark navy
background, pink accent).

GDPR / safety: the brief is already issue-level aggregate (no authors, no raw
post bodies, no personal data). This stage only re-expresses what the brief
already contains — the same aggregated civic items. Every page carries the
disclosure footer mirrored from presenter.DISCLOSURE.

CLI:  python scripts/site_builder.py [brief.json]   # builds from a brief file
"""
import html
import json
import re
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# Reuse the brief's existing label/region logic so we never reinvent mapping.
# presenter imports config_loader/requests; if that import fails for any reason
# (e.g. a missing optional dep in a stripped environment) we fall back to small
# local copies so the site stage still works and never blocks the pipeline.
try:
    import presenter  # noqa: E402

    SOURCE_LABELS = presenter.SOURCE_LABELS
    CATEGORY_LABELS = presenter.CATEGORY_LABELS
    ACTION_LABELS = presenter.ACTION_LABELS
    DISCLOSURE = presenter.DISCLOSURE
    _source_label = presenter._source_label
    _category_label = presenter._category_label
    _action_label = presenter._action_label
    _clean_constituency = presenter._clean_constituency
except Exception:  # pragma: no cover - defensive fallback
    presenter = None
    SOURCE_LABELS = {
        "google_news": "news", "local_news": "local news",
        "council_agenda": "agenda", "council_news": "agenda",
        "petition": "petition", "petitions": "petition",
        "planning": "planning", "planit": "planning",
        "fixmystreet": "street faults", "reddit": "reddit", "facebook": "facebook",
    }
    CATEGORY_LABELS = {
        "potholes_roads": "Roads/potholes", "bins_waste": "Bins/waste",
        "antisocial_behaviour": "Antisocial behaviour", "housing": "Housing",
        "transport": "Transport", "planning": "Planning", "nhs_access": "NHS access",
        "parks_environment": "Parks/environment", "safety_crime": "Safety/crime",
        "council_services": "Council services", "education": "Education",
        "other": "Other",
    }
    ACTION_LABELS = {"seed_motion": "seed motion", "outreach": "outreach",
                     "watch": "watch"}
    DISCLOSURE = ("— Aggregated from public sources only. Drafts for review; "
                  "nothing is posted or sent automatically.")

    def _source_label(i: dict) -> str:
        raw = i.get("source_type") or i.get("source_platform") or "?"
        return SOURCE_LABELS.get(raw, raw)

    def _category_label(i: dict) -> str:
        cat = i.get("category") or "other"
        return CATEGORY_LABELS.get(cat, cat.replace("_", " ").capitalize())

    def _action_label(i: dict) -> str:
        return ACTION_LABELS.get(i.get("suggested_action") or "watch",
                                 i.get("suggested_action") or "watch")

    def _clean_constituency(i: dict) -> str:
        return (i.get("constituency") or "").strip() or "Constituency unresolved"


# --------------------------------------------------------------------------- #
# Grouping helpers
# --------------------------------------------------------------------------- #
def _council_name(i: dict) -> str:
    """The council label for an item.

    Per the brief, the council is in `area` (sometimes suffixed " (London)"),
    falling back to the constituency name. We strip parenthetical suffixes so
    "Croydon (London)" and "Croydon" group together.
    """
    raw = (i.get("area") or "").strip()
    if not raw:
        raw = (i.get("constituency") or "").strip()
    raw = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    return raw or "Unattributed"


def slugify(name: str) -> str:
    """Stable, filesystem/URL-safe slug. Lowercase, hyphen-separated.

    Deterministic for a given name so council URLs never drift between runs.
    Non-ASCII letters (e.g. the non-breaking hyphen in 'Ashton-under-Lyne')
    are normalised to plain ASCII where possible.
    """
    s = name.strip().lower()
    # Normalise common unicode punctuation to ASCII before stripping.
    s = (s.replace("‑", "-").replace("–", "-").replace("—", "-")
         .replace("‘", "").replace("’", "")
         .replace("&", " and "))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "council"


def _trending(i: dict) -> bool:
    return bool(i.get("trending"))


def group_by_council(items: list) -> dict:
    """council name -> list of items, each council's items trending-then-score.

    Returns an insertion-ordered dict sorted by council display name so output
    is deterministic.
    """
    by_council: dict = {}
    for i in items or []:
        by_council.setdefault(_council_name(i), []).append(i)
    for name in by_council:
        by_council[name].sort(
            key=lambda x: (x.get("trending", 0), x.get("score", 0)), reverse=True)
    return {name: by_council[name] for name in sorted(by_council)}


def _headlines(its: list, n: int = 3) -> list:
    """Top-n short headlines/keywords for the dashboard preview."""
    out = []
    for i in its[:n]:
        s = (i.get("summary") or "").strip()
        out.append(s)
    return [s for s in out if s]


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #
def _e(text) -> str:
    return html.escape(str(text or ""), quote=True)


CSS = """
:root{
  --pink:#e4156b; --pink-light:#ff4d94; --navy:#0d1b2a; --black:#080f17;
  --mid-navy:#1a3550; --muted:#8aa4be; --steel:#4a6a8a; --white:#ffffff;
}
*{box-sizing:border-box;}
body{
  margin:0; background:var(--black); color:var(--white);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  line-height:1.5; -webkit-font-smoothing:antialiased;
}
a{color:var(--pink-light); text-decoration:none;}
a:hover{text-decoration:underline;}
.wrap{max-width:960px; margin:0 auto; padding:24px 18px 64px;}
header.top{border-bottom:1px solid var(--mid-navy); padding-bottom:18px; margin-bottom:24px;}
.eyebrow{color:var(--muted); font-size:13px; letter-spacing:.04em; text-transform:uppercase;}
h1{font-size:30px; font-weight:800; letter-spacing:-0.03em; margin:6px 0 4px;}
.sub{color:var(--muted); font-size:15px; margin:0;}
.bar{height:4px; border-radius:2px; background:linear-gradient(90deg,#e4156b,#ff4d94); margin:14px 0 0;}
.errors{background:var(--navy); border-left:3px solid var(--pink); color:var(--muted);
  padding:10px 14px; border-radius:6px; font-size:14px; margin:18px 0;}
.grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px;}
.card{display:block; background:var(--navy); border:1px solid var(--mid-navy);
  border-radius:10px; padding:18px; color:var(--white); transition:border-color .15s;}
.card:hover{border-color:var(--pink); text-decoration:none;}
.card h2{font-size:19px; font-weight:700; margin:0 0 8px; color:var(--white);}
.card .count{color:var(--muted); font-size:13px; font-weight:600;}
.card ul{margin:12px 0 0; padding:0 0 0 18px; color:var(--muted); font-size:14px;}
.card li{margin:0 0 5px;}
.flame{color:var(--pink-light);}
.item{background:var(--navy); border:1px solid var(--mid-navy); border-radius:10px;
  padding:18px; margin:0 0 16px;}
.item h3{font-size:17px; font-weight:700; margin:0 0 8px; color:var(--white);}
.tags{display:flex; flex-wrap:wrap; gap:8px; margin:0 0 10px;}
.tag{font-size:12px; font-weight:600; padding:3px 9px; border-radius:999px;
  background:var(--mid-navy); color:var(--muted);}
.tag.cat{color:var(--white);}
.tag.trend{background:linear-gradient(135deg,#e4156b,#ff4d94); color:var(--white);}
.meta{color:var(--steel); font-size:13px; margin:8px 0 0;}
.meta a{color:var(--pink-light);}
.action{color:var(--muted); font-size:14px; margin:8px 0 0;}
.action strong{color:var(--white);}
.back{display:inline-block; margin:0 0 18px; color:var(--muted); font-size:14px;}
.empty{background:var(--navy); border:1px solid var(--mid-navy); border-radius:10px;
  padding:28px; text-align:center; color:var(--muted);}
footer.disc{margin-top:40px; padding-top:18px; border-top:1px solid var(--mid-navy);
  color:var(--steel); font-size:12.5px;}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"robots\" content=\"noindex\">\n"
        f"<title>{_e(title)}</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n<div class=\"wrap\">\n"
        f"{body}\n"
        "<footer class=\"disc\">" + _e(DISCLOSURE) + "<br>"
        "Aggregated from public sources. Informational only — not official "
        "council communication. No personal data is shown.</footer>\n"
        "</div>\n</body>\n</html>\n"
    )


def _render_dashboard(brief: dict, by_council: dict, rel_prefix: str = "") -> str:
    d = _e(brief.get("date", ""))
    scanned = brief.get("posts_scanned", 0) or 0
    civic = brief.get("civic_items", 0) or 0
    n_councils = len(by_council)
    n_items = sum(len(v) for v in by_council.values())

    head = (
        "<header class=\"top\">\n"
        "<div class=\"eyebrow\">State Civic Intelligence</div>\n"
        f"<h1>Daily Civic Brief — {d}</h1>\n"
        f"<p class=\"sub\">Scanned {scanned:,} posts → {civic} civic issues. "
        f"Showing {n_items} across {n_councils} "
        f"council{'' if n_councils == 1 else 's'}.</p>\n"
        "<div class=\"bar\"></div>\n</header>\n"
    )

    errs = brief.get("errors") or []
    err_html = ("<div class=\"errors\">⚠ Source issues: "
                + _e("; ".join(errs)) + "</div>\n") if errs else ""

    if not by_council:
        body = (head + err_html
                + "<div class=\"empty\"><strong>No new civic issues today.</strong>"
                  "<br>The pipeline ran and found nothing worth briefing.</div>")
        return _page(f"Civic Brief — {brief.get('date', '')}", body)

    flame_span = '<span class="flame">🔥</span>'
    cards = []
    for name, its in by_council.items():
        slug = slugify(name)
        any_trend = any(_trending(i) for i in its)
        flame = " " + flame_span if any_trend else ""
        li_parts = []
        for i in its[:3]:
            mark = flame_span + " " if _trending(i) else ""
            li_parts.append("<li>" + mark + _e(i.get("summary", "")) + "</li>")
        lis = "".join(li_parts)
        plural = "" if len(its) == 1 else "s"
        cards.append(
            f"<a class=\"card\" href=\"{rel_prefix}councils/{slug}/index.html\">"
            f"<h2>{_e(name)}{flame}</h2>"
            f"<span class=\"count\">{len(its)} item{plural}</span>"
            f"<ul>{lis}</ul></a>")

    body = head + err_html + "<div class=\"grid\">\n" + "\n".join(cards) + "\n</div>"
    return _page(f"Civic Brief — {brief.get('date', '')}", body)


def _render_council(brief: dict, name: str, its: list) -> str:
    d = _e(brief.get("date", ""))
    mp = ""
    cons = ""
    for i in its:
        mp = mp or (i.get("mp_name") or "").strip()
        cons = cons or _clean_constituency(i)
    mp_bit = f" · MP: {_e(mp)}" if mp else ""
    cons_bit = f"{_e(cons)}" if cons and cons != "Constituency unresolved" else ""

    head = (
        "<a class=\"back\" href=\"../../index.html\">← Back to dashboard</a>\n"
        "<header class=\"top\">\n"
        "<div class=\"eyebrow\">State Civic Intelligence</div>\n"
        f"<h1>{_e(name)}</h1>\n"
        f"<p class=\"sub\">{len(its)} civic "
        f"item{'' if len(its) == 1 else 's'} — {d}"
        + (f" · {cons_bit}" if cons_bit else "") + mp_bit + "</p>\n"
        "<div class=\"bar\"></div>\n</header>\n"
    )

    blocks = []
    for i in its:
        cat = _e(_category_label(i))
        src = _e(_source_label(i))
        action = _e(_action_label(i))
        link = (i.get("source_link") or "").strip()
        i_mp = (i.get("mp_name") or "").strip()
        i_cons = _clean_constituency(i)
        trend_tag = ("<span class=\"tag trend\">🔥 Trending</span>"
                     if _trending(i) else "")
        link_html = (f"<a href=\"{_e(link)}\" target=\"_blank\" "
                     f"rel=\"noopener noreferrer\">source ({src})</a>"
                     if link else f"source ({src})")
        mp_meta = (f" · {_e(i_cons)}" if i_cons
                   and i_cons != "Constituency unresolved" else "")
        mp_meta += f" · MP: {_e(i_mp)}" if i_mp else ""
        blocks.append(
            "<div class=\"item\">"
            f"<div class=\"tags\"><span class=\"tag cat\">{cat}</span>"
            f"<span class=\"tag\">{src}</span>{trend_tag}</div>"
            f"<h3>{_e(i.get('summary',''))}</h3>"
            f"<p class=\"action\">Suggested action: <strong>{action}</strong></p>"
            f"<p class=\"meta\">{link_html}{mp_meta}</p>"
            "</div>")

    body = head + "\n".join(blocks)
    return _page(f"{name} — Civic Brief {brief.get('date', '')}", body)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def build(brief: dict, out_dir: Optional[Path] = None) -> None:
    """Build the static site from a brief dict into out_dir.

    Pure and deterministic. Writes index.html (dashboard) and one
    councils/<slug>/index.html per council. Never makes network calls.
    """
    out_dir = Path(out_dir) if out_dir is not None else (PROJECT_ROOT / "site")
    out_dir.mkdir(parents=True, exist_ok=True)

    items = brief.get("items") or []
    by_council = group_by_council(items)

    # Dashboard (hub).
    (out_dir / "index.html").write_text(
        _render_dashboard(brief, by_council), encoding="utf-8")

    # Council pages (spokes). Slugs are stable; collisions (two councils that
    # slug identically) are merged deterministically by appending.
    councils_dir = out_dir / "councils"
    seen_slugs: dict = {}
    for name, its in by_council.items():
        slug = slugify(name)
        if slug in seen_slugs and seen_slugs[slug] != name:
            # Extremely rare: disambiguate to avoid clobbering another council.
            n = 2
            while f"{slug}-{n}" in seen_slugs:
                n += 1
            slug = f"{slug}-{n}"
        seen_slugs[slug] = name
        page_dir = councils_dir / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(
            _render_council(brief, name, its), encoding="utf-8")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        briefs = sorted((PROJECT_ROOT / "data").glob("brief_2*.json"))
        path = str(briefs[-1]) if briefs else None
    if not path:
        print("No brief JSON found. Usage: python scripts/site_builder.py [brief.json]")
        sys.exit(1)
    with open(path) as f:
        brief = json.load(f)
    out = PROJECT_ROOT / "site"
    build(brief, out)
    n = len(group_by_council(brief.get("items") or []))
    print(f"Built site at {out} — dashboard + {n} council page(s) for {brief.get('date', '')}.")
