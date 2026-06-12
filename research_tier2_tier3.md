# Phase 3 research — news feeds, webcasts, radio (June 2026)

Pilot areas: Croydon, Lewisham, Hackney, Waltham Forest, Ealing.
Rule applied throughout: the spec bends to reality. Items 1–3 (petitions,
PlanIt, FixMyStreet) were confirmed and built in Phase 2 — see
research_new_sources.md. This file covers the new research only.

## 4. Google News RSS — CONFIRMED, with one spec correction

- Query-feed format current and stable (verified against multiple 2026 guides):
  `https://news.google.com/rss/search?q=<query>&hl=en-GB&gl=GB&ceid=GB:en`
- Supports boolean OR, quoted phrases, `when:2d` recency, `intitle:`.
- **Spec correction:** the 302-redirect-to-hash-URL behaviour affects the
  named *topic/location section* feeds (`/rss/headlines/section/geo/...`).
  Plain `/rss/search?q=` query feeds do NOT redirect, so no hash capture is
  needed. We use query feeds only — one per borough:
  `"Croydon" AND (council OR planning OR housing OR bins OR crime OR NHS OR roadworks) when:2d`
- Caveats: feeds return ~70–100 items max; links are Google redirect URLs
  (store them as-is — they resolve for the reader); titles end with
  " - Publisher" (strip for the classifier, keep for disclosure).
- No auth, no documented rate limit; one fetch per borough per day is
  far inside polite use. Headlines + links only, same GDPR posture as
  the existing news source.
- Live verification happens through the existing candidate->verified probe
  (scripts/verify_feeds.py) on the Mac, same as every other feed.

## 5. ModernGov/CMIS per council — MIXED (the site-wide feed is dead everywhere)

Phase 2's live probe: `mgRss.aspx?bcr=1` returns an HTML page (not RSS) on
all five hosts. Per-council findings:

| Council | Host | Status |
|---|---|---|
| Lewisham | councilmeetings.lewisham.gov.uk | **Per-committee RSS CONFIRMED** — lewisham.gov.uk/about-this-site/rss-feeds lists ~44 committee feeds (Full Council, Mayor & Cabinet, Planning A/B, all select committees + local assemblies). Page last updated Jan 2025, still live. |
| Croydon | democracy.croydon.gov.uk | ModernGov confirmed; per-committee feed URLs unconfirmed -> probe |
| Hackney | hackney.moderngov.co.uk | ModernGov confirmed; per-committee unconfirmed -> probe |
| Waltham Forest | democracy.walthamforest.gov.uk | ModernGov confirmed; per-committee unconfirmed -> probe |
| Ealing | ealing.moderngov.co.uk AND ealing.cmis.uk.com | Both hosts exist — Ealing appears to run CMIS alongside/instead of ModernGov. CMIS has no standard RSS -> probe both, expect to rely on Google News + ealing.news instead |

Build plan: `scripts/discover_council_feeds.py` (run once on the Mac) fetches
Lewisham's RSS index page and extracts the real per-committee feed URLs, and
probes the standard ModernGov per-committee RSS patterns on the other hosts
for their main committees. Whatever verifies goes into targets.yaml; whatever
doesn't is recorded as dead. No assumptions.

## 6. Webcasts (Tier 2) — platforms found, but caption access is NOT clearly permitted

Where each council streams:

| Council | Platform(s) |
|---|---|
| Croydon | Public-i (croydon.public-i.tv) + Civico (webcasting.croydon.gov.uk) + YouTube archive |
| Lewisham | Public-i (lewisham.public-i.tv) |
| Hackney | Civico (civico.net/hackney) + YouTube (youtube.com/hackneycouncil) |
| Waltham Forest | YouTube (youtube.com/c/CouncilWalthamForest) + Civico (civico.net/walthamforest) |
| Ealing | YouTube — meetings streamed live, recordings up within 24h |

**The blocker, reported plainly:** YouTube's Terms of Service prohibit
automated access and downloading of content (including captions) except via
the official API — and the official Data API only allows caption download by
the *video owner*. Tools like youtube-transcript-api use unofficial
endpoints, which is exactly the kind of scraping-around we don't do.
Public-i and Civico expose no public caption/transcript API at all.

**The clean route:** councils own these recordings and generally *want*
scrutiny of their meetings. A short written request to each council's
democratic services team asking permission to retrieve captions/transcripts
of public meetings for aggregate civic analysis cures the ToS problem for
YouTube (owner permission) and opens doors at Public-i/Civico councils
(some have transcripts on request). Build webcast_source.py only for
councils that say yes.

## 7. Radio (Tier 3) legal position — NOT DEFENSIBLE, recommend drop

- The UK TDM exception (s29A CDPA 1988) covers text-and-data mining for
  **non-commercial research only**. state.com's use is commercial analysis.
- The Dec 2024–Feb 2025 government consultation proposed a broad commercial
  TDM exception with opt-out; most of the 11,520 responses rejected it. The
  March 2026 government report commits only to further evidence-gathering —
  no legislative change is imminent.
- Recording broadcast audio (live stream or BBC Sounds catch-up) for internal
  commercial analysis therefore has no statutory cover, and BBC terms
  prohibit recording/redistribution of streams.
- **Plain answer, as requested: it is not defensible today. Drop Tier 3** and
  revisit only if the law changes. The design notes below are kept so nothing
  is lost if it does.

## 8. Whisper sizing (for the record, given §7)

On Apple Silicon (M1-class): `large-v3-turbo` via whisper.cpp or
faster-whisper runs ~4x realtime — a 2-hour show transcribes in ~30 minutes,
~2 GB RAM. `small` is similar speed, noticeably lower accuracy, 8 GB-safe.
If a *permitted* audio source ever appears (e.g. a council providing meeting
audio directly), the shared transcription utility should default to
large-v3-turbo with `small` as the low-RAM fallback.

## Sources
- Google News RSS parameters: newscatcherapi.com "Google News RSS Search
  Parameters: The Missing Docs"; wprssaggregator.com (2026); hasdata.com (2026)
- Lewisham feeds: lewisham.gov.uk/about-this-site/rss-feeds
- Croydon webcasts: croydon.gov.uk live-broadcasts page; croydon.public-i.tv;
  webcasting.croydon.gov.uk
- Hackney: civico.net/hackney; youtube.com/hackneycouncil
- Waltham Forest: youtube.com/c/CouncilWalthamForest; civico.net/walthamforest
- Ealing: youtube.com/@EalingCouncil; ealing.cmis.uk.com
- YouTube ToS: youtube.com/static?template=terms; developers.google.com/youtube/terms
- TDM/copyright: gov.uk "Copyright and Artificial Intelligence" consultation +
  March 2026 Report; DLA Piper and Kluwer Copyright Blog analyses (2026)
- Whisper: Apple Silicon benchmarks (voicci.com), whisper.cpp/faster-whisper
  comparisons (2026)
