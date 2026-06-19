# Handoff prompt — build the per-council static site + dashboard, deploy to Netlify

> Paste everything below the line into a fresh Cowork/Claude session that has file access
> to the `state-civic-intelligence` project folder. It is written to be self-contained.

---

You are picking up an established Python project called **State Civic Intelligence** and
adding a new output stage to it. Read this whole brief before touching anything, then build.

## What the project already does

It is a daily UK civic-listening pipeline. Each morning it:

1. **Scrapes** public sources (Google News RSS, ICNN local news, UK petitions, PlanIt
   planning applications, FixMyStreet, ModernGov/CMIS council agendas) — `scripts/*_source.py`
   and `scripts/reddit_scraper.py` / `facebook_scraper.py`.
2. **Classifies** each post with an LLM via OpenRouter (`scripts/classifier.py`) into civic
   categories, with urgency/specificity/summary.
3. **Maps** each item to a UK council and parliamentary constituency + MP
   (`scripts/constituency_mapper.py`).
4. **Scores & groups** the items (`scripts/scorer.py`).
5. **Presents** a readable brief (`scripts/presenter.py`) and writes two files per day to
   `data/`:
   - `brief_<YYYY-MM-DD>.json` — the structured brief (THIS is your input).
   - `brief_message_<YYYY-MM-DD>.md` — a Telegram-ready prose version.
6. Delivers the brief to Telegram via an external agent (Hermes). Not relevant to you.

The orchestrator is `scripts/run_pipeline.py`. Config is two merged YAML files in `config/`
(`targets.yaml` hand-maintained pilot, `targets_national.yaml` generated national); load via
`scripts/config_loader.py`. The DB is SQLite at `data/state_intel.db` and holds only
issue-level aggregates + post IDs (no raw text, no personal data).

## Your task

Add a **site builder** stage that turns the daily brief into a static website:

- **One dashboard page** (`index.html`) — the single home URL. Shows every council that has
  items today as a card or row: council name, its top trending issue headlines/keywords,
  item count, a marker (🔥) for trending issues. Each council is a link to its own page.
- **One page per council** (e.g. `councils/croydon/index.html`) — full detail for that one
  council: every civic item with headline/summary, category, source link, suggested action,
  trending flag, and the MP/constituency. Include a "back to dashboard" link.
- The dashboard is the hub; council pages are the spokes. Clicking a council on the dashboard
  opens its page; each council page links back.

Then wire it up so the site **regenerates every day** when the pipeline runs, and **deploys to
Netlify** automatically.

## CRITICAL — the brief only holds the top 40 items; the site needs the full set

`data/brief_<date>.json` contains only the top-scored items (config `limits.top_n`, currently
40) — that's right for the Telegram digest but far too thin for per-council pages (40 items
across all UK = mostly empty council pages). A real run produces ~1,600 civic items, but the
pipeline discards everything below the top 40 before writing to disk.

**Your FIRST task** is therefore a small pipeline change: in `scripts/run_pipeline.py`, after
classification/mapping/scoring, write the FULL mapped+classified civic item list (the `items`
variable, ~1,600 rows, before the `top_n` cut) to a new file `data/civic_items_<date>.json`
(same per-item shape as below). Leave the existing top-40 `brief_<date>.json` untouched so the
Telegram path is unaffected. The site builder reads `civic_items_<date>.json`, NOT the brief.
Confirm with the user before changing pipeline behaviour, and guard the new write so it can't
break the existing flow.

## Input data shape (read a real file first)

Each item (in both `brief_*.json` `items[]` and the new `civic_items_*.json`) looks like:

```json
{
  "date": "2026-06-19",
  "posts_scanned": 2920,
  "civic_items": 47,
  "errors": ["source: message", "..."],
  "items": [
    {
      "category": "housing",
      "area": "Croydon",
      "constituency": "Croydon East",
      "mp_name": "Natasha Irons",
      "summary": "Council to buy remaining flats in a local block.",
      "source_link": "https://...",
      "source_type": "google_news",
      "trending": 1,
      "score": 12.0,
      "suggested_action": "seed_motion",
      "volume": 1,
      "engagement": 0
    }
  ]
}
```

Group items by council. The council label is in `area` (sometimes suffixed " (London)") or
recoverable from `constituency`. Reuse the region/council logic already written in
`scripts/run_pipeline.py` (`_constituency_regions`, `_item_region`) and
`scripts/presenter.py` (`_grouped`, `SOURCE_LABELS`, `CATEGORY_LABELS`, `ACTION_LABELS`) —
do not reinvent label mapping; import or mirror those.

## How to build it

1. Create `scripts/site_builder.py` with a `build(brief: dict, out_dir: Path) -> None`
   function: pure, deterministic, no network, no LLM. It reads the brief dict and writes
   static HTML into `out_dir` (default `site/`). Slugify council names for folder paths
   (lowercase, hyphens). Generate self-contained HTML (inline CSS, no build step) so Netlify
   serves it as-is. Keep it clean and scannable; mobile-friendly.
2. Add a final stage to `scripts/run_pipeline.py.run()` after the presenter block: call
   `site_builder.build(brief, PROJECT_ROOT / "site")`. Guard it in try/except so a site
   failure never blocks the brief (match the existing presenter guard pattern).
3. Write `scripts/test_site_builder_offline.py` — offline tests (no network): builds from a
   sample/real brief, asserts `index.html` exists and contains every council, each council
   page exists and contains its items' source links, slugs are stable, and an empty brief
   produces a valid "no issues today" dashboard. Follow the style of
   `scripts/test_presenter_offline.py`.

## Deploy to Netlify

Recommend and set up the simplest reliable path. Two options — pick and explain:
- **Netlify CLI deploy** from the pipeline (`netlify deploy --prod --dir=site`) after build, OR
- **Git-based deploy**: commit the `site/` folder; Netlify auto-builds on push (the user,
  Mitesh, does the actual `git push` — the agent has no git credentials).

The user already has the project on a Mac. Walk them through: creating the Netlify site,
connecting it, where the public URL comes from, and how the daily cron run keeps it fresh.
Ask the user whether they own a custom domain before configuring one.

## Hard constraints (do not violate)

- **Public aggregate data only.** The site shows the same aggregated civic items the brief
  already contains. No raw post text, no usernames, no personal data, no transcripts.
- **Disclosure always.** Every page must carry a footer line stating the data is aggregated
  from public sources and is informational. Mirror the `DISCLOSURE` text in `presenter.py`.
- **Drafts/automation, human approves.** This is an internal/monitoring site; never present
  it as official council communication.
- **Python 3.9 venv.** NEVER use `X | None` style type unions. Add
  `from __future__ import annotations` at the top of any new module that needs modern hints.
- **Never commit, print, or share secrets.** `.env` is gitignored; leave it alone.
- **Fail gracefully.** The site stage must never crash the pipeline.

## Definition of done

- `python scripts/site_builder.py` builds `site/` from the latest brief and the pages open
  correctly in a browser (dashboard → council page → back).
- `python scripts/test_site_builder_offline.py` passes.
- `run_pipeline.py` regenerates the site each run, guarded against failure.
- Netlify deploy works and the user has a single public dashboard URL that updates daily.
- Disclosure + no-personal-data constraints visibly satisfied on every page.

Build a local prototype from the latest brief first and show the user before wiring up the
Netlify deploy.
