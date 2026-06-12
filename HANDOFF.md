# State Civic Intelligence — Plain-English Explainer & Handoff

This document does two jobs: it explains the whole system in simple language for Mitesh,
and it is the briefing prompt for any new Claude instance continuing the work.
Repo: https://github.com/Mitesh-Autopilot99/state-civic-intelligence (main @ 2308dcc, Phase 4).

---

## 1. What this product is

A daily "civic listening" system for the UK. Every morning it reads thousands of public
sources — local news, petitions, planning applications, street-fault reports, council
meeting agendas — works out which items are real local civic issues (potholes, housing,
bin collections, NHS access, crime, planning rows), maps each one to a parliamentary
constituency and its MP, scores them by importance, and sends a short ranked brief to
Mitesh's Telegram at 9am. The target user is someone who needs to know "what is angering
people in each constituency today" — the way an MP's office would.

It now covers **all 650 UK constituencies** (Phase 4), up from a 5-constituency pilot.

## 2. How the pieces fit — the simple version

Think of it as a factory line with five stations. Each station is one Python script in
`scripts/`. A Python script is just a text file of instructions; running
`python scripts/run_pipeline.py` makes the computer execute those instructions top to
bottom. The scripts share data through a small SQLite database file (`data/`) and a YAML
config file (YAML = a human-readable settings file).

**Station 1 — Scrape.** Each source module fetches new public items and returns them in
one common shape (title, body, link, area, engagement numbers). Sources never store
personal data — names of commenters etc. are never requested ("aggregate and discard").

**Station 2 — Cap.** We classify with free OpenRouter LLM models, which have daily
limits. So `apply_volume_caps` keeps only the ~300 highest-engagement items per day,
shared fairly across sources so Google News can't drown out petitions.

**Station 3 — Classify.** An LLM reads each item and answers: is this a civic issue?
What category (housing, roads, NHS…)? One-line summary? Suggested action (watch /
seed_motion)? Items that aren't civic are dropped.

**Station 4 — Map.** `constituency_mapper.py` turns a place into a constituency:
postcodes go through postcodes.io (a free API), place names are matched carefully (never
guess — a wrong constituency is worse than "unresolved"), and the MP's name is attached
from the Parliament Members API.

**Station 5 — Score & brief.** `scorer.py` ranks issues (petitions weighted highest,
then planning, then news) and keeps the top 40. `run_pipeline.py` prints the brief
grouped by region (London, Scotland, North West…), showing only constituencies that
actually have items.

## 3. The sources, one by one

- **Google News RSS** (the baseline): for every council we build a search-feed URL like
  `news.google.com/rss/search?q="Croydon"+(council OR planning OR housing...)`. RSS is
  an old, simple format where a website lists its latest items as structured XML — easy
  for scripts to read. ~357 council feeds, 2,500+ items/day. Known noise: same-named
  places abroad (e.g. an "Ashfield Park" in Ireland) sneak in.
- **ICNN independent local news** (the quality layer — the product's real power, since
  these titles are what MPs actually read about their patch): seeded from the Independent
  Community News Network's member map. Their website renders the member list with
  JavaScript, so instead we download the Google My Maps KML export (one request, ~120
  outlets with name, website, map pin), guess each outlet's WordPress feed URL, and label
  it with a council — by name match first, else by reverse-geocoding the map pin via
  postcodes.io. Unlabelled outlets get `status: needs_label` and are not polled.
- **UK Parliament petitions**: open data API. We flag petitions where a constituency
  signs far more than its share (over-indexing = local anger), top 5 seats per petition.
- **PlanIt (planning applications)**: free aggregator of council planning registers. We
  keep applications with public comments or contention keywords. Nationally it rotates
  through ~380 councils 60 per run (full cycle ≈ a week) because PlanIt rate-limits;
  on a 429 ("too many requests") the pass stops and resumes next day where it left off.
- **FixMyStreet (street faults)**: mySociety RSS per council (~720 verified feeds). It
  works on trends — it learns each council's normal daily report volume and only flags
  spikes. Day 1 of national mode just builds baselines, so it emits nothing at first.
- **Council meeting agendas (ModernGov)**: many councils run the same "ModernGov"
  committee software which exposes RSS. `discover_council_feeds.py --national` guesses
  each council's host (democracy.X.gov.uk etc.), verifies feeds live, and saves progress
  so it can be interrupted and resumed. It probes 8 councils in parallel (polite: each
  worker talks to a different server). Full run done: **118 hosts, 470 verified feeds**;
  188 councils have no guessable ModernGov host.
- **Council meeting agendas (CMIS)**: for the ModernGov misses, many big councils
  (Birmingham, Dudley, Warrington, Colchester…) run "CMIS" committee software instead,
  which has no usable RSS — but its meetings-calendar page is server-rendered HTML.
  `discover_cmis_feeds.py` found and verified **17 CMIS sites** (16 live-verified);
  `cmis_source.py` parses each calendar (one GET per council per day), keeps key
  committees meeting in the next 8 days, and emits them as `council_agenda` items —
  identical shape to ModernGov, so the rest of the pipeline doesn't know the difference.
  GDPR: reads only committee names, dates, rooms — organisations, never people.
  Remaining gap: councils on neither system (Belfast, Brighton, Liverpool…) need a
  later hand-curated third pass.
- **Facebook groups** (via Apify, logged-out only) — pilot-scale. **Reddit** — disabled
  until API keys are added. **Radio** — designed but off. **Council webcasts (Tier 2)**
  — blocked until permission emails are sent (drafts exist; never send autonomously).

## 4. The two-file config

- `config/targets.yaml` — small, hand-maintained pilot file. Wins all conflicts.
- `config/targets_national.yaml` — big, generated by `build_national_targets.py` from
  reference data (`fetch_national_data.py` downloads all 650 constituencies + every UK
  council from mySociety). Discovery/seeding scripts append to it.
- `config_loader.py` merges the two so every script sees one config. Feeds have statuses:
  `candidate` → `verified` (only verified are polled) → `dead` / `needs_label`.
  Re-running any generator/discovery script is safe (idempotent — it skips what exists).

## 5. Hermes (delivery) and the daily schedule

Hermes Desktop is an agent app on the Mac that runs scheduled jobs and talks to Telegram.
Its job here: at 9am run the pipeline and send the brief file to Mitesh on Telegram.
Its instruction files (SOUL.md, AGENTS.md, skills) live in the repo. **Known bug:** the
Hermes LLM currently wraps the brief in its own reasoning chatter and sends it twice —
the planned fix is to make it send the brief file verbatim. The bigger planned upgrade is
a **presenter stage**: collection/classification stays as-is, then a separate LLM rewrites
the raw brief into something genuinely readable before Telegram delivery.

## 6. How we work (process for the new instance)

- Claude edits code in the shared folder `state-civic-intelligence/`; **Mitesh runs the
  scripts on his Mac** and pastes terminal output back; Mitesh commits and pushes
  (Claude's sandbox has no git credentials, but can run git status/diff locally).
- Claude tests everything offline first: `python scripts/test_national_offline.py`
  (54 checks) plus the older per-source test scripts. All currently pass.
- **Mac environment traps:** the venv is Python 3.9 — never write `X | None` type
  annotations (add `from __future__ import annotations` if needed). The urllib3
  LibreSSL warning on every run is harmless. Real secrets (OpenRouter, Apify) live in
  `.env`, which is gitignored — never commit, print, or share them.

## 7. Hard constraints (verbatim, non-negotiable)

- Never post autonomously; no logged-in scraping; aggregate-and-discard; disclosure always.
- Never republish or quote transcripts anywhere.
- Flag anything that contradicts this prompt; the spec bends to reality.
- NotebookLM may be used for post-aggregation briefs only, never raw transcripts.
- GDPR posture: organisations not people; only public aggregate data; no personal data stored.

## 8. Where things stand right now (June 2026)

Done and pushed (2308dcc): the full national layer — reference data, generated national
config, 720 verified FixMyStreet/Google News feeds, PlanIt rotation, petitions national
mode, volume caps, region-grouped brief, ICNN KML seeder (code-complete), ModernGov
national discovery COMPLETE (118 hosts, 470 verified agenda feeds), CMIS agenda source
COMPLETE (17 sites incl. Birmingham, 16 verified live, already emitting items — first
run produced 11 upcoming key-committee meetings). First national pipeline run succeeded
end-to-end: 2,563 posts → 126 classified → 62 civic items → top 40 brief.

**Pending, in order:**
1. **The presenter stage** (next big build): separate LLM turns the raw brief into a
   polished, human-worthy Telegram message; also fixes the Hermes double-send bug.
2. First live run of `python scripts/seed_local_news.py` (the KML parser is offline-tested
   but has never seen the real KML) — review labels, fix any `needs_label` entries.
3. A few daily runs so FixMyStreet baselines mature and the brief fills out.
4. User-side: send the 5 webcast permission emails; add Reddit API keys when ready.
5. Nice-to-have: hand-curated agenda pass for non-ModernGov/non-CMIS councils (Belfast,
   Brighton — Brighton is ModernGov on the non-standard host present.brighton-hove.gov.uk;
   Liverpool, Newcastle…); investigate the 4 "host answered but no CMIS pages" councils
   (Moray, North Lanarkshire, Stirling, South Tyneside); retry the 29 dead FixMyStreet
   feeds using council alt-names; filter foreign same-named-place noise in the classifier
   or presenter.

---

*To continue in a new chat: share this file (or point Claude at the repo folder) and say
"continue from HANDOFF.md". The full code is the source of truth — read README.md and the
scripts before changing anything.*
