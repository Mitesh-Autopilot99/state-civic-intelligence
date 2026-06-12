# Project: State Civic Intelligence System

Working directory: this folder. The deterministic pipeline is plain Python in
`scripts/` — run it, don't reimplement it. You are the orchestrator, interface,
and drafter.

## Commands

- Run the daily pipeline + show brief: `python scripts/run_pipeline.py`
- Verify/re-rank subreddit targets:    `python scripts/verify_targets.py`
- Init/inspect DB:                     `python scripts/db.py` · DB at `data/state_intel.db`
- Logs: `logs/pipeline_YYYY-MM-DD.log` · Briefs: `data/brief_YYYY-MM-DD.json`

## Daily brief workflow

1. The cron job runs `python scripts/run_pipeline.py --cron` as its pre-run script.
2. If it wakes you, read the brief JSON at the path given in context and deliver a
   formatted Telegram brief: for each item — area, one-line issue summary, source
   link, engagement, constituency + MP name, suggested action (seed_motion /
   outreach / watch). Lead with trending items. Keep it scannable.
3. If the pipeline reported errors, state them in one line at the top and propose
   a fix — do not apply fixes without approval.

## Drafting workflows (always end in chat for approval)

- "draft a motion for item N" → use the motion-draft skill.
- "draft outreach for item N" → use the outreach-draft skill (disclosure line mandatory).
- "draft the pulse page for [constituency]" → use the pulse-page skill.
- After approval, the human sends/publishes. You never do.
- Record outreach outcomes Mitesh reports into the `outreach_log` table.

## Approval gate

Approve / edit / reject happens in Telegram chat. Treat "approved" as permission
to hand the final text back for human sending — never as permission to send.

## Compliance

The five hard constraints in SOUL.md apply to every task in this project. The
compliance skill restates them; load it whenever drafting or modifying code.
