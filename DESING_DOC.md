
# Job Alert Bot — Design Doc

## 1. Overview

A scheduled, serverless pipeline that watches GitHub repos publishing new-grad / job posting lists, filters postings down to ones the user actually wants (EU-based roles, or US roles that sponsor internationals), and pushes new matches to a Discord channel via webhook.

No server, no database service, no hosting cost — everything runs inside GitHub Actions on a cron schedule, with state persisted back into the same repo.

## 2. Goals / Non-goals

**Goals**

- Detect new postings within ~1 run cycle (e.g. every 30–60 min)
- Filter to: EU-based roles (any country), OR US-based roles explicitly marked as sponsoring internationals
- Never re-notify the same posting twice
- Zero infrastructure to maintain (no VPS, no external DB)
- Easy to add/remove source repos or tweak filter rules

**Non-goals (v1)**

- Applying to jobs automatically
- A web UI / dashboard
- Perfect sponsorship classification — v1 accepts some false positives/negatives in exchange for simplicity

## 3. Architecture

```
 ┌─────────────────────┐
 │  GitHub Actions cron │  (e.g. */30 * * * *)
 └──────────┬───────────┘
            │ triggers
            ▼
 ┌─────────────────────┐
 │   fetch_and_parse    │  pull raw JSON/README from source repos
 └──────────┬───────────┘
            ▼
 ┌─────────────────────┐
 │       filter         │  location + sponsorship rules
 └──────────┬───────────┘
            ▼
 ┌─────────────────────┐
 │   dedupe vs state    │  compare against seen.json
 └──────────┬───────────┘
            ▼
 ┌─────────────────────┐
 │  notify_discord      │  POST new matches to webhook
 └──────────┬───────────┘
            ▼
 ┌─────────────────────┐
 │  commit updated      │  seen.json pushed back to repo
 │  state back to repo  │
 └─────────────────────┘
```

Everything is one Python script (or a few small modules) invoked by a workflow file. State lives in a JSON file committed to the repo itself — no external database needed at this scale (a few hundred to low thousands of postings tracked).

## 4. Data Sources

Target repos that publish postings as structured data (JSON preferred, README table as fallback):

| Repo                            | Format                            | Notes                                |
| ------------------------------- | --------------------------------- | ------------------------------------ |
| SimplifyJobs/New-Grad-Positions | `.github/scripts/listings.json` | Has sponsorship emoji tags           |
| SimplifyJobs/Summer-Internships | same pattern                      |                                      |
| vanshb03/New-Grad-2026          | README table                      | May need HTML/markdown table parsing |
| (others as discovered)          | varies                            | Config-driven, see §8               |

Each source gets an adapter function that returns a normalized posting object:

```json
{
  "id": "stable-hash-of-company+title+url",
  "company": "string",
  "title": "string",
  "location": "string (raw, as published)",
  "url": "string",
  "sponsorship_flag": "yes | no | unknown",
  "source_repo": "string",
  "date_posted": "ISO date if available"
}
```

Normalizing early means the filter and notify stages never need to know which repo a posting came from.

## 5. Filtering Logic

Two independent checks, combined with OR:

**A. EU location match**

- Match `location` against a maintained list of EU country names, major EU city names, and common abbreviations ("Remote (EU)", "London" — note: UK is a judgment call, likely include since often bundled with EU hiring).
- Simple substring/regex match; case-insensitive; strip diacritics.

**B. US role + sponsorship**

- Match `location` against US state/city patterns.
- Then check `sponsorship_flag`:
  - Repos like Simplify tag rows with an emoji (e.g. 🛂) indicating sponsorship offered, or a strikethrough/note indicating "no sponsorship" — parse this directly where available.
  - Where no explicit signal exists, default to `unknown`.
  - **v1 behavior**: `unknown` sponsorship on US postings → excluded by default (configurable). This avoids spamming with roles that likely won't sponsor.
  - **v1.1 option**: for `unknown` cases, optionally send the raw posting text to an LLM classification call to guess sponsorship likelihood from phrasing ("must be authorized to work without sponsorship" → exclude; no mention → weak include). Flag these as "unverified" in the Discord message so you can sanity-check.

A posting passes the filter if `(A) OR (B and sponsorship == yes)` — with the `unknown` handling above as a tunable knob in config.

## 6. State Management (dedupe)

- `state/seen.json` — array or set of posting `id`s already notified.
- On each run: fetch → parse → filter → subtract `seen.json` → notify only the diff → append new ids to `seen.json` → commit.
- Commit is done by the workflow itself (using `GITHUB_TOKEN`, no extra PAT needed) via `git commit` + `git push` at the end of the job, or via `stefanzweifel/git-auto-commit-action`.
- Prune `seen.json` periodically (e.g. drop ids older than 90 days) so it doesn't grow unbounded — postings this old are irrelevant anyway.

## 7. Discord Notification

Sent via a Discord **webhook URL** (no bot hosting needed) using `discord.com/api/webhooks/.../...`.

Format as an embed per posting, or batch multiple into one message if several appear in the same run:

```json
{
  "embeds": [
    {
      "title": "Company — Role Title",
      "url": "https://...",
      "description": "📍 Location · 🛂 Sponsors internationals",
      "color": 5814783
    }
  ]
}
```

Batch new postings from a single run into one webhook call (Discord embeds support up to 10 per message) to avoid rate limits and notification spam.

## 8. Repository Structure

```
job-alert-bot/
├── .github/
│   └── workflows/
│       └── check_jobs.yml
├── config/
│   ├── sources.yaml        # list of source repos + adapter type
│   └── eu_locations.yaml   # country/city match list
├── src/
│   ├── fetch.py            # per-source adapters
│   ├── normalize.py
│   ├── filter.py
│   ├── notify_discord.py
│   └── main.py             # orchestrates the pipeline
├── state/
│   └── seen.json
└── requirements.txt
```

Adding a new source repo = adding an entry to `sources.yaml` + (if needed) a small adapter function — no changes to filter/notify logic.

## 9. GitHub Actions Workflow (sketch)

```yaml
name: check-jobs
on:
  schedule:
    - cron: "*/30 * * * *"
  workflow_dispatch: {}

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python src/main.py
        env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "chore: update seen.json"
          file_pattern: state/seen.json
```

`workflow_dispatch` included so you can trigger a manual run to test.

## 10. Secrets & Config

- `DISCORD_WEBHOOK_URL` — stored as a repo secret, injected as an env var. Never committed.
- Everything else (source list, EU location list, sponsorship handling mode) lives in plain YAML config files so it's editable without touching code.

## 11. Edge Cases & Failure Modes

| Case                                                      | Handling                                                                                                                       |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Source repo changes its JSON/README structure             | Adapter fails gracefully, logs a warning, skips that source for the run rather than crashing the whole pipeline                |
| Discord webhook rate-limited                              | Batch messages (§7); add basic retry with backoff                                                                             |
| Duplicate postings across two source repos                | Dedupe key based on`company+title` normalized, not just per-source id                                                        |
| Workflow doesn't run (GitHub Actions cron delay/downtime) | Acceptable for personal use; cron minimum practical interval is ~5 min but 15–30 min is more realistic/polite to source repos |
| `seen.json` merge conflicts (unlikely, single workflow) | Not a concern since only one job writes to it sequentially                                                                     |

## 12. Future Enhancements

- LLM-based sponsorship classification for `unknown` cases (§5.B)
- Per-role keyword filtering (e.g. only "backend", "data engineer")
- Slack/email as alternate notification channels
- Web dashboard reading from `seen.json` for browsing history
- Weekly digest mode in addition to real-time alerts
