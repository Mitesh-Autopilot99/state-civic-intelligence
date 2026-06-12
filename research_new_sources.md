# Phase 2 Research — Four Structured Civic Sources
Date: 2026-06-12 · Status: pre-build research, per spec ("the spec bends to reality")

Target areas (pilot): Croydon, Lewisham, Hackney, Waltham Forest, Ealing (London boroughs),
plus the national petitions source which covers every constituency at once.

---

## 1. UK Parliament Petitions API — CONFIRMED, build as specced

- **List endpoint:** `https://petition.parliament.uk/petitions.json?state=open` — paged JSON
  (`data[]` with `attributes.action`, `background`, `signature_count`, `links.self` per petition,
  `links.next` for paging).
- **Detail endpoint:** `https://petition.parliament.uk/petitions/{id}.json` — verified live; contains
  `attributes.signatures_by_constituency`: `[{name, ons_code, mp, signature_count}, ...]`
  (one entry per Westminster constituency, with MP name included — we don't even need our own
  MP lookup for these items).
- **Over-index flag:** for each open petition, national per-constituency average =
  total UK signatures / 650. Flag where target-constituency share ≥ N× that average
  (propose N=2.0, tunable in config).
- **Auth/rate limits:** no auth, no documented rate limit. We poll once/day, ~1 list pass
  (a few pages, filtered to petitions above a signature floor) + detail fetch per candidate.
  Well within fair use.
- **Licence:** Open Government Licence v3.0 (site footer). Free to reuse with attribution.
- **GDPR:** zero personal data — counts only. MP names are public officials.

## 2. PlanIt API — CONFIRMED, one adjustment

- **Endpoint:** `https://www.planit.org.uk/api/applics/json?auth={Area}&recent={days}&pg_sz=...`
  All five target boroughs are listed planning authorities on PlanIt (Croydon, Lewisham,
  Hackney, Waltham Forest, Ealing all present).
- **Fields:** `description`, `address`, `area_name`, `start_date`, `app_state`, `app_size`,
  `app_type`, `link`, `url`, `postcode`, `location_x/y`; `other_fields` includes **`n_comments`**
  (number of public comments) and `ward_name`.
- **Adjustment to spec:** PlanIt exposes a **comment count** (`n_comments`) but no separate
  objection count, and it's optional (missing where the council doesn't publish it).
  Plan: rank by `n_comments` where present; where absent, fall back to `app_size`
  (Large/Medium) + keyword cues in description. Flagging this as the spec's
  "comment/objection counts" — comments only, by reality.
- **Rate limits:** explicit — max 5,000 results/request, 1MB/response, 429 + Retry-After on
  excess. One query per borough per day with `pg_sz≈50` is trivially inside limits.
- **Licence:** free API, donation-supported aggregator of public planning registers
  (public-record data). Internal aggregate use is fine; we link back to source.
- **GDPR:** PlanIt itself already refuses to store applicant/agent/case-officer names
  ("For Data Protection reasons this value is not stored") — convenient: the source is
  pre-sanitised. We additionally never read applicant/agent address fields.

## 3. FixMyStreet — SPEC BENDS: open dataset is stale; live RSS is the path

- **Finding:** the open "FixMyStreet Geographic Counts" dataset (data.mysociety.org /
  github mysociety/fms_geographic_data) **ends at 2020**. Useless for "up 3× this month".
- **Live path:** fixmystreet.com publishes per-council and per-ward RSS feeds of new reports
  (standard platform feature, linked from /alert and each council's "All reports" page;
  pattern `/rss/reports/{Council}` and ward-level variants). All five boroughs are
  on fixmystreet.com (none of the five runs a separate cobranded instance that would
  fragment the data — Croydon, Lewisham, Hackney, Waltham Forest, Ealing all listed).
- **Plan:** poll each borough's RSS daily; store ONLY (category-ish title keywords, ward/area,
  date, count) into our own `fms_daily_counts` aggregate table; compute trends from OUR
  accumulated counts (e.g. 7-day vs prior 21-day per category+area). Trend items only enter
  the brief once we have ≥2 weeks of baseline. Report titles pass through the classifier
  in-memory the same as social posts; reporter names are not in feed titles, and raw text
  is discarded after classification as usual.
- **Exact feed URLs:** to be confirmed by the probe script at build time (run on the Mac,
  which fetches each candidate feed and reports status) — my research tooling couldn't
  render the postcode-gated alert pages, but feed existence is documented platform behaviour.
- **Coordinate-level research data:** requires a request to mySociety research team
  (alex.parsons@mysociety.org / data release documentation). NOT needed for the trend
  feature above. A draft request is prepared (see below) if we later want LSOA-level history.
- **Licence:** site content reuse for internal aggregate monitoring with linkback; mySociety
  is a charity — their data releases are for research with documentation. We are not
  republishing report content.

## 4. Council ModernGov/CMIS + local news RSS — MIXED, probe per council

- **Lewisham: CONFIRMED.** lewisham.gov.uk/about-this-site/rss-feeds lists per-committee RSS
  for ~45 committees incl. Planning A/B, Strategic Planning, Mayor & Cabinet, Full Council,
  and ward assemblies (Catford South, Deptford etc. — ward-level civic gold).
  ModernGov host: councilmeetings.lewisham.gov.uk.
- **Croydon:** democracy.croydon.gov.uk is ModernGov (mgWhatsNew.aspx exists). RSS link
  could not be confirmed remotely (page is JS-rendered for my fetcher). Standard ModernGov
  installs expose RSS (`mgRss.aspx?...` per committee). → probe at build time.
- **Hackney:** hackney.moderngov.co.uk confirmed ModernGov. RSS unconfirmed → probe.
- **Waltham Forest / Ealing:** democracy portals not yet checked → probe.
- **Probe plan:** `scripts/verify_feeds.py` (new) — takes candidate feed URLs from
  config, fetches each, validates RSS/Atom, reports verified/dead. Same
  candidate→verified pattern as targets.yaml. Run once on the Mac; only verified feeds
  are polled daily.
- **Local news RSS (LDRS/independents):** all WordPress-based locals expose `/feed`:
  Inside Croydon (insidecroydon.com), Hackney Citizen (hackneycitizen.co.uk),
  853 (853.london, SE London incl. parts of Lewisham); candidates for Waltham Forest Echo
  and Ealing titles to be added to config and probed the same way. Headlines + links only.
- **GDPR/copyright:** headlines and agenda-item titles only, never article bodies;
  bylines never stored. Classifier sees title in-memory; brief stores summary + link.

---

## Proposed scoring weights (needs approval — task #17)

Two changes to `scorer.py`:

1. **Per-source engagement normalisation.** Current `eng_norm` divides by the average
   engagement across ALL issues. Petition signature counts (thousands) would swamp
   Facebook likes (tens). Fix: compute `eng_norm` within each `source_type` pool,
   same 3.0 cap. No behaviour change when only social sources are present.

2. **Additive source-intent bonus** (a petition signature or planning objection is a
   stronger motion-intent signal than a comment):

   | source_type        | bonus |
   |--------------------|-------|
   | petition (over-indexing locally) | +2.0 |
   | planning (high n_comments)       | +1.5 |
   | fixmystreet trend                | +1.0 |
   | council_agenda / local_news      | +0.75 |
   | reddit / facebook (social)       | +0.0 (baseline) |

   Existing formula otherwise unchanged:
   `score = eng_norm*2 + urgency + specificity + freshness + uniqueness*2 + min(volume,5) + source_bonus`

## Build order (unchanged from spec — research supports it)

petitions → PlanIt → FixMyStreet RSS → council/news RSS.
Each lands with: one test command, plain-English success criteria, graceful-failure
wrapper in run_pipeline.py (same as Facebook), `source_type` tagged end-to-end.

## Draft request to mySociety (only if we later want coordinate-level history)

> To: alex.parsons@mysociety.org
> Subject: Research data request — FixMyStreet coordinate-level data, London boroughs
>
> Hi, I work for state.com on a civic-issues monitoring pilot covering five London
> boroughs (Croydon, Lewisham, Hackney, Waltham Forest, Ealing). We aggregate
> issue-level trends (category × area × time) to brief local civic engagement work;
> we store no personal data — names and free text are discarded after classification.
> We'd like to request the coordinate-level FixMyStreet research dataset described in
> your data release documentation, for those boroughs, to build a historical baseline
> for category/area trend detection. Happy to sign your data-release terms and share
> our Legitimate Interests Assessment. — Mitesh Thaker, state.com
