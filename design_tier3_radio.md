# Tier 3 design: radio_source.py — DESIGN ONLY, switched off by default

Status: **not built, not enabled**. Legal research (research_tier2_tier3.md §7)
found recording broadcast audio for this project's analysis is **not
defensible today**: the s29A CDPA TDM exception covers non-commercial research
only, no commercial exception is imminent (March 2026 government report =
more evidence-gathering), and BBC terms prohibit recording/redistribution of
streams. This document preserves the design so nothing is lost if the
position changes.

## Switch-on condition (the legal test — all of one row, not a mix)

The module may be enabled only when ONE of these is in writing, kept in the
repo alongside this file:

1. **Written legal advice** that state's specific use qualifies as
   non-commercial research under s29A CDPA 1988 (the test is the *purpose of
   the analysis*, not what we call it — our spec described internal
   commercial analysis, which is why it fails today); or
2. **A licence/permission from the broadcaster** covering recording and
   text-and-data analysis of the named programmes; or
3. **A statutory change** creating a commercial TDM exception that covers
   this use (track gov.uk "Copyright and AI" follow-ups).

Until then `radio: enabled: false` stays in targets.yaml and run_pipeline
never imports the module.

## Module design (for when the test passes)

Target programmes: BBC Radio London phone-ins (the five boroughs' issues
surface there) — exact shows configured, not hard-coded.

Flow, mirroring every other source:

1. `radio_source.py` reads `radio:` block from targets.yaml
   (enabled flag, shows[], max_hours_per_day, model override).
2. Capture: ONLY via whatever route the permission/licence specifies
   (e.g. broadcaster-provided audio or catch-up access). No stream-ripping
   fallback, ever.
3. Transcribe locally via shared `transcribe.py` utility (below). Audio file
   deleted immediately after transcription.
4. Chunk transcript into ~5-minute segments; each becomes one classifier item
   (`source_type: "radio"`, `body` = segment text, truncated to the
   classifier's existing 400-char window).
5. Classifier/mapper/scorer unchanged. Suggested scorer bonus: 0.5
   (broadcast curation, but second-hand and fuzzy on location).
6. **Aggregate-and-discard, hardened:** the raw transcript exists in memory
   only. Nothing but issue summaries/areas/volumes/links is written to
   SQLite. Caller names: the classifier prompt for radio items adds
   "never include personal names in summary or area". A post-filter strips
   capitalised name patterns from summaries as a second net. Transcripts are
   never republished or quoted anywhere — including the brief.

Acceptance (from spec): one transcribed phone-in processed end-to-end with
zero caller names and zero raw transcript text stored — verified by grepping
the DB and brief JSON for any 6+ word sequence from the transcript.

## Shared transcription utility: scripts/transcribe.py

- Engine: faster-whisper (or whisper.cpp), model **large-v3-turbo** default —
  ~4× realtime on Apple Silicon, 2-hour show ≈ 30 min, ~2 GB RAM.
- Fallback `small` for low-RAM machines (8 GB-safe), config flag.
- Interface: `transcribe(path) -> list[{"start": s, "end": s, "text": str}]`,
  deletes nothing itself — callers own file lifecycle.
- Also serves Tier 2 if a council supplies raw audio instead of captions —
  that use is fine today because the council (rights-holder) gave permission.
- VPS note: only build/run transcription after the VPS migration if the VPS
  has the RAM; otherwise it stays a Mac-side step.

## What we will NOT do (restating the hard constraints)

No logged-in scraping, no stream-ripping around BBC terms, no storing audio
or transcripts, no quoting callers, no autonomous posting. The spec bends to
reality; today reality says Tier 3 stays off.
