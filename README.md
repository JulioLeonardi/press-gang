# Job Alert Bot

Watches new-grad/internship job repos, filters to **EU roles** (UK excluded) or **US roles that sponsor internationals**, and posts new matches to Discord. Runs entirely on GitHub Actions cron — no server, no database.

Implements [DESING_DOC.md](DESING_DOC.md), with corrections noted below.

## Setup

1. Push this to a GitHub repo.
2. Create a Discord webhook: *Channel → Edit Channel → Integrations → Webhooks → New Webhook → Copy URL*.
3. Add it as a repo secret named `DISCORD_WEBHOOK_URL` (*Settings → Secrets and variables → Actions*).
4. Ensure *Settings → Actions → General → Workflow permissions* is set to **Read and write** so the bot can commit `state/seen.json`.
5. Trigger a manual run from the Actions tab to confirm it works.

Local run:

```bash
pip install -r requirements.txt
python src/main.py --dry-run        # log matches, send nothing, write nothing
python tests/test_core.py           # sanity checks
```

## How it decides

A posting is kept if **(EU location)** OR **(US location AND sponsorship offered)**.

US detection runs **first and wins**. This matters more than it sounds: Dublin CA/OH, Berlin NH, Paris TX, Vienna VA and Naples FL are all real US tech locations that would otherwise false-positive as EU.

Multi-location postings qualify if *any one* location matches, so a "London | Milan | NYC" role comes through on Milan.

Tunable in [config/eu_locations.yaml](config/eu_locations.yaml) and [config/sources.yaml](config/sources.yaml) — no code changes needed to add a source, a city, or flip a rule.

## Dedupe key

`sha1(normalized_company + normalized_title + MMDDYYYY_posting_date)`

- **Date included** so the same role reposted months later is treated as genuinely new.
- **URL and source id excluded** — the same job appears in multiple repos with different tracking URLs and different UUIDs. Keying on either would notify you twice for one job. In testing this collapsed 7 cross-repo duplicates out of 68 matches.
- **Company suffixes, case, emoji and diacritics normalized**, so `Acme, Inc.` and `ACME` are one job.
- When a source publishes **no date**, the date the bot first saw the posting is used. Without that fallback the slot would be empty and a repost would silently collide with the original — the exact case the date is there to catch.

## First run

The first run **seeds state silently and sends nothing**. With ~33,000 postings across the sources, notifying on the backlog would mean hundreds of Discord messages.

Every run afterward notifies only on new matches. To re-seed from scratch, run the workflow with the `reseed` input checked, or `python src/main.py --reseed`.

`max_notifications_per_run` (default 60) caps a single run. If a source reformats and suddenly looks like 5,000 new jobs, you get 60 and the rest defer — not a channel flood.

## Weekly heartbeat

This bot's healthy state is silence, which looks identical to a dead bot — exhausted Actions minutes, a source gone permanently 404, a revoked webhook. [heartbeat.yml](.github/workflows/heartbeat.yml) posts a status message every Monday 09:00 UTC so silence becomes informative:

- postings tracked, and how many were new in the last 7 days
- estimated Actions minutes used this month, with a progress bar
- last check-jobs result, and a red flag if several recent runs failed
- a warning if run durations approach 60s (crossing it doubles minute usage)

Run it manually with `gh workflow run heartbeat.yml -f dry_run=true` to preview without sending.

**The minutes figure is an estimate.** GitHub's `/timing` endpoint reports `billable: 0ms` on the Free tier, so it's unusable. Instead the heartbeat counts runs this month, samples the real `run_duration_ms` of recent runs, applies GitHub's round-up-to-the-minute rule, and scales. Two caveats: minutes are billed **account-wide** across all your private repos, and this only counts this one; and the count includes Dependabot's runs, which are slower than the bot's own.

## Cost and cadence

Every job bills as a **whole minute**, rounded up, even though a check takes ~16 seconds. Against the Free tier's 2,000 private-repo minutes/month:

| Cadence | Minutes/month | % of free tier |
| --- | --- | --- |
| Every 30 min | ~1,460 | 73% |
| **Hourly (current)** | **~730** | **37%** |
| Every 2 hours | ~365 | 18% |

Hourly is the configured default: the sources only update a few times a day, so anything faster bought nothing and left no headroom.

Exhausting the allowance doesn't charge you — the default spending limit is $0, so Actions simply stops until the next billing cycle. That's precisely the silent failure the heartbeat exists to catch.

**On making the repo public** (which would give unlimited minutes): the secret stays safe, since the workflows trigger only on `schedule` and `workflow_dispatch` and fork PRs can't reach secrets. The real cost is privacy — `state/seen.json` and the Actions logs would publicly expose which jobs you're tracking, with dated commits showing when you started looking. If you do go public, strip `company`/`title` from the state file first; only the hashed ids are load-bearing.

## Corrections to the design doc

The doc was written against assumed source formats. Verified against live data, several assumptions were wrong:

| Doc said | Actually |
| --- | --- |
| Sponsorship marked with a 🛂 emoji in JSON | A 4-value string enum: `Offers Sponsorship`, `Does Not Offer Sponsorship`, `U.S. Citizenship is Required`, `Other` |
| `location` is a string | `locations` is a **list** |
| `date_posted` is an ISO date | A **unix epoch int** |
| Dedupe on `company+title+url` (§4) vs `company+title` (§11) | Contradictory. Resolved to `company+title+date`, no URL |
| `seen.json` is an array of ids | Can't be — §11's 90-day prune needs a timestamp. Stored as `{id: {first_seen, company, title}}` |

Also worth knowing:

- **99.3% of Simplify rows have sponsorship `Other`** (unspecified), and only 22 of 18,364 say `Offers Sponsorship`. The explicit-sponsorship path is therefore very narrow — most of your volume will come from the EU branch. Setting `allow_unknown_sponsorship: true` opens the floodgates and is off by default.
- **84% of rows are inactive** (closed postings). Filtered out via `require_active`.
- The README tables publish dates as `Aug 05` with **no year**. The year is inferred as the most recent one that isn't in the future.
- `SimplifyJobs/Summer2027-Internships` currently serves a byte-identical file to `Summer2026` — it's a mirror, so it's not configured as a separate source.

## Failure behavior

- A source that 404s or changes shape is logged and skipped; other sources still run. (Verified — a dead URL during development skipped cleanly.)
- If *every* source fails, the run aborts **without touching state**, so nothing is falsely marked seen.
- Discord 429s honor the `retry_after` the API returns; 4xx rejections don't retry.
- Only postings that actually sent are marked seen — a failed batch is retried next run rather than lost.
- An unreadable `state/seen.json` aborts the run rather than re-notifying everything.

## Layout

```
.github/workflows/check_jobs.yml   hourly cron + manual trigger
.github/workflows/heartbeat.yml    weekly liveness report
config/sources.yaml                source repos, adapters, tunables
config/eu_locations.yaml           country/city/remote match lists
src/fetch.py                       per-source adapters
src/normalize.py                   text/date normalization, dedupe key
src/filter.py                      location + sponsorship rules
src/notify_discord.py              batched embeds, retry/backoff
src/main.py                        orchestration
src/heartbeat.py                   weekly status + minutes estimate
state/seen.json                    committed state
tests/test_core.py                 sanity checks
```
