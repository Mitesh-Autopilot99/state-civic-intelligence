# State Civic Intelligence System — Runbook

A listening engine for state.com. Every morning it can tell you which local civic
issues are heating up in which UK constituencies, from public social media only.
It drafts; humans approve and send. It never posts anywhere by itself.

**Pilot setup:** everything runs on your Mac inside Hermes Desktop. Manual trigger
is the primary way to run it; the 9am schedule fires as a bonus when the laptop is
awake. Section 8 covers the later VPS move.

---

## 1. One-time install

```bash
cd state-civic-intelligence
bash setup_laptop.sh
```

This creates a Python environment, the database, and installs the Hermes skills,
SOUL.md and AGENTS.md. Then do sections 2–5 once.

## 2. OpenRouter — account, $10 credit, API key (~5 minutes)

1. Go to **openrouter.ai** → Sign up (Google login is fine).
2. Top-right avatar → **Credits** → **Add credits** → add **$10** (card or PayPal).
   This is a one-time deposit, not a subscription. It permanently raises your
   free-model limit from 50 to 1,000 requests/day; the $10 just sits there unused.
3. Avatar → **Keys** → **Create key** → name it `state-intel` → copy the key
   (starts `sk-or-`).
4. Put it in `.env` as `OPENROUTER_API_KEY=sk-or-...`

Model check (free models change weekly): visit openrouter.ai/collections/free-models
and confirm the two models in your `.env` still exist; if not, pick two current
free instruct models and update `CLASSIFIER_MODEL` / `CLASSIFIER_FALLBACK_MODEL`.
You can also just ask Hermes: "check OpenRouter's free model list and suggest
updates to my .env" — approve before it edits.

## 3. Reddit — request API access, then register the app

> ⚠ **Since 11 Nov 2025 (Responsible Builder Policy), Reddit no longer issues
> self-serve API keys.** reddit.com/prefs/apps shows a policy notice instead of
> creating the app. You must request access and be approved first (reportedly
> 2–4 weeks). Do NOT scrape without approval — that now clearly violates their
> policy.

1. File a request at the Reddit API request form:
   support.reddithelp.com/hc/en-us/requests/new?ticket_form_id=14868593862164
   Describe the use case honestly: read-only monitoring of ~15–20 public UK
   city subreddits, ~20–30 requests/day, no posting ever, aggregate-and-discard
   (no usernames or raw text stored), no user profiling, no AI training, for
   state.com (brand-affiliated — say so if asked).
2. Once approved and you can create the app (script type, redirect uri
   `http://localhost:8080`), grab:
   - the **client ID** — short string under the app name ("personal use script")
   - the **secret** — labelled "secret"
3. Put both in `.env`, with your Reddit username in `REDDIT_USER_AGENT`:
   ```
   REDDIT_CLIENT_ID=abc123
   REDDIT_CLIENT_SECRET=xyz789
   REDDIT_USER_AGENT=macos:state-civic-listener:v1.0 (by /u/yourusername)
   ```
4. While waiting: everything else (OpenRouter, Telegram, Hermes, cron job) can
   be set up now. The pipeline will simply report "no verified subreddits"
   until the Reddit keys arrive.

## 3b. Facebook public groups via Apify (~10 minutes, optional)

Runs alongside (or before) Reddit — uses Apify's free tier ($5 usage credit
every month, no card needed). The pipeline's daily post budget (~30 posts/day)
is sized to stay inside that credit.

1. Sign up free at **console.apify.com** → Settings → **API & Integrations** →
   copy your Personal API token (starts `apify_api_`).
2. Put it in `.env`: `APIFY_TOKEN=apify_api_...`
3. Add groups to `config/targets.yaml` under `facebook_groups:` (template is in
   the file). **Public groups only.** Facebook login-walls logged-out visitors
   even for public groups, so check the privacy *label* instead: open the group
   while logged in as yourself (human browsing is fine — the logged-out rule is
   for scraping, which Apify does) and look under the group name for
   **"Public group"**. Private groups can't be scraped by the actor and must
   not be added.
4. Test: `python scripts/run_pipeline.py` — the log shows
   "Facebook: N raw -> M keyword-matched".

Notes: same GDPR rules as Reddit — author names and comments are never read or
stored. If `APIFY_TOKEN` is empty or no groups are verified, the Facebook
source silently skips and the rest of the pipeline runs normally. If the
monthly Apify credit runs out, the source errors but the brief still arrives.

## 3c. Structured civic sources (no keys, no cost — built in)

Four additional sources run automatically alongside social. None needs an
account, key, or payment; all are public, logged-out, and licensed for reuse
(OGL v3.0 / public record / RSS made for syndication).

| Source | What it surfaces | Tag in brief |
|---|---|---|
| UK Parliament petitions | Petitions over-indexing (≥2× national avg) in our 15 target constituencies | `petition` |
| PlanIt planning apps | Contested applications (high comment counts, large schemes, contention keywords) in the 5 boroughs | `planning` |
| FixMyStreet trends | Category spikes (e.g. potholes up 3× in Croydon this week) from daily aggregate counts — never individual reports | `fixmystreet` |
| Council + local news RSS | ModernGov committee agendas and local-press headlines (title + link only) | `council_agenda` / `local_news` |
| Google News RSS | One query feed per borough (`"Croydon" AND (council OR planning OR ...) when:2d`) — catches outlets we don't subscribe to directly. Publisher names stripped from titles; Google redirect links stored as-is | `google_news` |

**One-time setup** — verify the RSS feed URLs (FixMyStreet + council/news +
Google News start as `status: candidate`; only verified feeds are polled):

```bash
python scripts/verify_feeds.py            # probes each feed once, flips candidate -> verified/dead
python scripts/discover_council_feeds.py  # finds REAL per-committee ModernGov feeds (the
                                          # site-wide bcr=1 feed is dead on all 5 councils):
                                          # learns the URL pattern from Lewisham's published
                                          # index, probes the other hosts, writes only what
                                          # verifies into config/targets.yaml
```

**Test each source standalone** (same pattern as Reddit):

```bash
python scripts/petitions_source.py      # flagged petitions with local counts
python scripts/planit_source.py         # contested planning apps per borough
python scripts/fixmystreet_source.py    # records today's counts; trends after 14 days of history
python scripts/council_news_source.py   # civic headlines + agenda items
```

Offline tests (no network, run anywhere): `python scripts/test_<source>_offline.py`.

Notes: scoring weights structured sources above social (petition +2.0,
planning +1.5, fixmystreet +1.0, news/agendas/google_news +0.75) because they are
verified civic signal, not inferred. FixMyStreet needs ~2 weeks of daily runs
before it can emit trends — silence from it early on is normal. Each source
fails gracefully: one being down never blocks the brief (errors appear at the
top of the brief instead). Temporarily disable any source with
`SOURCES_DISABLE=reddit,facebook python scripts/run_pipeline.py`.

GDPR is structural here too: petitions are aggregate by construction; PlanIt
is queried with a field list that never requests applicant/agent/officer
names; FixMyStreet stores only (day, council, category, count); news feeds are
read title+link only — bylines and bodies are never fetched.

## 4. Telegram bot (~5 minutes)

1. In Telegram, message **@BotFather** → send `/newbot` → pick a display name
   (e.g. "State Intel") and a unique username ending in `bot` (e.g. `state_intel_bot`).
2. Copy the token BotFather returns (`123456789:ABC...`). Keep it secret.
3. Message **@userinfobot** → it replies with your numeric user ID.
4. Add both to `~/.hermes/.env` (Hermes's env file, not the project one):
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABC...
   TELEGRAM_ALLOWED_USERS=<your numeric ID>
   ```
5. Start the gateway: `hermes gateway` (or in Hermes Desktop, enable Telegram in
   settings). Message your bot — it should reply.
6. In your DM with the bot, send `/sethome` — scheduled briefs deliver here.

## 5. Verify targets, first run, and the cron job

```bash
source .venv/bin/activate
python scripts/verify_targets.py    # checks every candidate subreddit is real + active
python scripts/verify_feeds.py      # checks every candidate RSS feed (FixMyStreet + council/news)
python scripts/run_pipeline.py      # first manual run — prints the brief
```

**Success looks like:** a ranked table of verified subreddits, then a printed
brief with items showing area, summary, constituency, MP, and a source link.
First run may take a few minutes (classification batches).

Then create the scheduled job. Paste this into chat with Hermes (Desktop or Telegram):

> Create a cron job named "daily-brief": schedule "0 9 * * *", workdir
> /FULL/PATH/TO/state-civic-intelligence, enabled toolsets terminal and file only,
> attached skills state-compliance and state-brand-voice, pre-run script
> ".venv/bin/python scripts/run_pipeline.py --cron", delivery telegram. Prompt:
> "Read the brief JSON at the path given in context and deliver the formatted
> daily civic brief per AGENTS.md. If errors are present, summarise them first
> and propose a fix without applying it."

Also raise the script timeout in `~/.hermes/config.yaml`:

```yaml
cron:
  script_timeout_seconds: 900
```

## 6. Daily use

| You want | Do this |
|---|---|
| Run today's brief now (primary) | Tell Hermes: **"run today's brief"** (or `/cron run daily-brief`, or terminal: `python scripts/run_pipeline.py`) |
| Draft a motion | "draft a motion for item 3" |
| Draft outreach | "draft outreach for item 3" |
| Pulse page | "draft the pulse page for Croydon East" |
| Approve/reject | Reply approve / edit: ... / reject in chat |
| Add/remove a subreddit | Edit `config/targets.yaml` (add with `status: candidate`), run `python scripts/verify_targets.py` |
| Add/remove an RSS feed | Edit `config/targets.yaml` (`fixmystreet:` or `council_news:`, `status: candidate`), run `python scripts/verify_feeds.py` |
| Disable a source temporarily | `SOURCES_DISABLE=facebook,planning python scripts/run_pipeline.py` (names: reddit, facebook, petitions, planning, fixmystreet, council_news) |
| Pause the schedule | `/cron pause daily-brief` · resume with `/cron resume daily-brief` |

The 9am schedule only fires if the laptop is awake and the Hermes gateway is
running. Manual trigger is the reliable path during the pilot.

## 7. When something goes wrong

- **No brief arrived:** laptop asleep or gateway not running. Run manually:
  `python scripts/run_pipeline.py`. Check `logs/pipeline_<today>.log`.
- **"No verified subreddits":** run `python scripts/verify_targets.py`.
- **"No VERIFIED ... feeds" in the log:** run `python scripts/verify_feeds.py` once.
- **One source erroring (e.g. PlanIt 429):** the brief still arrives with the
  error listed at the top; the source usually recovers next run. Persistent?
  Disable it (`SOURCES_DISABLE=planning`) and investigate via its standalone script.
- **FixMyStreet emits nothing:** normal for the first ~14 days — it needs
  baseline history before it can call something a spike.
- **Classifier errors / model gone:** free models rotate. Update the two model
  names in `.env` (see §2) — or ask Hermes to diagnose; approve its patch.
- **Reddit 401/403:** check the three REDDIT_ values in `.env`; regenerate the
  secret at reddit.com/prefs/apps if needed.
- **Telegram silent:** check `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_USERS` in
  `~/.hermes/.env`; restart the gateway.
- **Spot-check GDPR (do this during acceptance week):**
  ```bash
  sqlite3 data/state_intel.db ".schema issues"   # no author/raw-body columns exist
  sqlite3 data/state_intel.db "SELECT summary FROM issues LIMIT 10"  # issue summaries, no usernames
  ```

## 8. Migrating to a VPS later (post-pilot)

1. Buy a small VPS (Hostinger ~$8–10/mo, Ubuntu 22.04, 2GB RAM is plenty).
2. `scp -r state-civic-intelligence/ user@vps:~/` (includes `.env` and
   `data/state_intel.db` — your history moves with you).
3. SSH in, install Hermes (Linux script from hermes-agent.nousresearch.com), then
   `bash setup_vps.sh`.
4. Copy `TELEGRAM_*` values into the VPS `~/.hermes/.env`; run `hermes gateway`;
   `/sethome` again from your phone.
5. Recreate the cron job (§5) — now with schedule `0 7 * * *` for the pipeline
   run and 9am delivery if you want the original two-step timing.
6. Confirm one brief arrives, then disable the laptop cron job (`/cron remove daily-brief`).
   No code changes — the scripts, skills, config and DB are identical on both machines.

## 9. Compliance summary (read once, remember forever)

No autonomous posting anywhere · public logged-out sources only · aggregate and
discard (no authors, no raw bodies — the DB schema physically has no columns for
them; planning applicants, FixMyStreet reporters and journalist bylines are
never even fetched) · disclosure in every state.com mention · all drafts pass through human
approval · write the one-page Legitimate Interests Assessment before switch-on
(template task: ICO LIA template + this system's data flows) · Nextdoor only via
the official developer programme application.
