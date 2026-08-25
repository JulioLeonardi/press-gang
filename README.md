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

A posting is kept if its **title isn't excluded** AND **(EU location)** OR **(US location AND sponsorship is plausible)**.

Title exclusion runs first and is driven by `exclude_title_patterns` in [config/sources.yaml](config/sources.yaml) — internship terms, clearance markers (`TS/SCI`, `poly`, `ITAR`, `US citizen`…), and non-CS role families (sales, field service, retail, back-office). Terms match **whole words** against the normalized title, so `intern` drops "Software Engineer Intern" but leaves "International Tax Analyst" and "Internal Tools Engineer" alone, and `sci` never touches "Computer Science". That whole-word rule is why each suffix form is listed separately.

The list is **global** — it prunes the US sources too. Before adding a term, check it against both pools; several obvious-looking ones are traps, and each is recorded inline in the config with the CS role it would have cost:

| Tempting term | What it kills | Used instead |
|---|---|---|
| `assurance` | Quality **Assurance** Engineer | — |
| `hardware` | SWE New Grad – **Hardware** Tools | `hardware engineer` |
| `assistant` | Research **Assistant**/Programmer | — |
| `marketing` | ML New Grad (…**Marketing** Analytics) | `marketing digital` |
| `commercial` | Forward Deployed SWE – **Commercial** | — |
| `recruitment` | SWE AI/LLM – Global Frontier Tech **Recruitment** Program | `recruiter` |
| `support` | AI / Application **Support** Engineer | — |

[tests/test_core.py](tests/test_core.py) asserts all of these still pass the filter, so a retune that reintroduces a bare term fails the suite instead of quietly costing you CS roles.

US detection runs **first and wins**. This matters more than it sounds: Dublin CA/OH, Berlin NH, Paris TX, Vienna VA and Naples FL are all real US tech locations that would otherwise false-positive as EU.

Multi-location postings qualify if *any one* location matches, so a "London | Milan | NYC" role comes through on Milan.

Tunable in [config/eu_locations.yaml](config/eu_locations.yaml) and [config/sources.yaml](config/sources.yaml) — no code changes needed to add a source, a city, or flip a rule.

## Sources

| Source | Adapter | Format |
|---|---|---|
| `SimplifyJobs/New-Grad-Positions` | `simplify_json` | 12.9MB `listings.json` |
| `vanshb03/New-Grad-2026` | `markdown_table` | README pipe table |
| `Aramente/eu-tech-jobs` | `eu_parquet` | daily Parquet snapshot |

The first two are US new-grad lists and between them produce **zero** EU matches — the EU half of the bot was dead until `eu-tech-jobs` was added. It's a different kind of source and the adapter reflects that:

- **Parquet, read over HTTP Range requests.** The file is 19.2MB but 17.8MB of that is a `description_md` column the bot never displays. Reading only the needed columns pulls **1.5MB in ~9 requests**. It's a single row group, so column pruning is the only lever available. This is the sole reason `pyarrow` is a dependency; if the import fails, that one source is skipped and the run continues.
- **Two files.** `jobs.parquet` carries only a `company_slug` (`wttj-capgemini`), so `companies.parquet` (78KB, plain GET) is joined for display names.
- **It's a firehose, not a curated list.** ~20k live rows, no new-grad concept, and the upstream LLM tagger lags the scraper — `role_family` is null on 50% of rows and `seniority` on 75%, including nearly every brand-new row. Untagged rows are therefore **kept**, since requiring a tag would discard exactly the newest postings. Because untagged rows bypass the `role_family` allowlist, the global `exclude_title_patterns` list is the second line of defence: the `options:` block gets the source to ~132 new/day and the title terms take it to **~73/day**. Per-option measurements are recorded in the config.
- **Four of its columns are dead.** `stack` and `languages` are empty on all 20,120 rows, `visa_sponsorship` is 100% null, and `remote_policy` is null on 92.8%. None of them are usable as filters, despite what the published schema suggests. Company `categories` is nearly as flat — 1,474 of 1,732 companies are just `tech`.
- **Consumer fashion/beauty brands are dropped.** The upstream repo carries ~400 of them for a separate private landing page it runs — ~1,750 live jobs, mostly Paris-based, so they pass the EU location check. Its own public site filters them on the same `industry_tags`.

The `visa_sponsorship` gap is harmless here: these are EU-located roles, so they pass `filter_postings` on location, not on sponsorship.

## The nightly board

Discord is a queue — good for "what's new in the last hour", useless for browsing. [scripts/render_board.py](scripts/render_board.py) renders every currently-open match into one self-contained HTML page, and [.github/workflows/board.yml](.github/workflows/board.yml) rebuilds it nightly and publishes it to GitHub Pages.

```bash
python scripts/render_board.py -o site/index.html    # then open site/index.html
```

Search, per-tier filter chips, and a reviewed-checkbox with a progress meter; review state lives in `localStorage`, so it's per-browser and survives the nightly rebuild (ids that leave the board are pruned from it).

**It shows more than the channel does.** The board forces `allow_unknown_sponsorship: true`, so the *sponsorship unverified* tier is visible here even though `config/sources.yaml` keeps it out of Discord. That's the point: it's where you look at what the bot deliberately withheld. Rows the bot first saw within the last 24h are badged **NEW**.

| Tier | Current | Meaning |
|---|---|---|
| Sponsorship confirmed | 7 | The listing itself says so |
| EU location | 1,536 | No visa question (UK excluded) |
| Known H-1B employer | 958 | Company files petitions (USCIS FY22–23) |
| Sponsorship unverified | 756 | No signal either way — **not in Discord** |

Postings older than 60 days are dropped (`--max-age-days`); at ~3,300 rows the page is ~850KB, all inline, no external requests.

### Setting up Pages

Pages on a **private** repo needs a paid plan; on a public repo it's free, and public repos also get unlimited Actions minutes (your hourly cron currently costs ~730 of the 2,000 free private-repo minutes).

1. *Settings → Pages → Build and deployment → Source:* **GitHub Actions**.
2. Run the `job-board` workflow once from the Actions tab.
3. The URL appears in the workflow's `deploy` job summary — `https://<user>.github.io/<repo>/`.

The cron is `0 0 * * *` (00:00 UTC = 8 PM ET during EDT). GitHub cron has no timezone support, so it drifts to 7 PM local when the US falls back to EST; change it to `0 1 * * *` in November or accept the hour.

**The published page is world-readable at a guessable URL.** It ships `<meta name="robots" content="noindex, nofollow">` so it stays out of search results, but that's obscurity, not access control — anyone with the link sees your whole job search. `site/` and `backlog.md` are gitignored so neither gets committed, but `state/seen.json` still would; see the note below on stripping it.

## Sponsorship: why there's a company list

The sources' own `sponsorship` field is close to useless. Measured against the live pool:

- **28** active US postings say "Offers Sponsorship" — that's ~0.08/day, with some weeks at zero.
- **8** say citizenship is required.
- **~99%** say `Other`, which means *unspecified*, not *offered*.

So gating on the field makes the bot silent, and ignoring it floods the channel with cleared-defense roles that were never going to sponsor. [config/h1b_sponsors.yaml](config/h1b_sponsors.yaml) is the middle path: a company-level signal seeded from the **USCIS H-1B Employer Data Hub** (FY2022+FY2023), matched against the names that actually appear in the feeds and hand-cleaned, since USCIS records legal entities (`AMAZON.COM SERVICES LLC`) while postings use trade names (`Amazon`).

US postings resolve in this order:

| Source flag | Company on the list | Result |
|---|---|---|
| `no` | — | dropped (citizenship required) |
| `yes` | — | sent, verified |
| `unknown` | yes | sent, **🛂 Known H-1B employer** |
| `unknown` | no | sent ⚠️ unverified, or dropped if `allow_unknown_sponsorship: false` |

That last row is the volume dial: `true` gives ~50/day with the good ones visually distinguishable, `false` gives ~28/day of known sponsors only.

**This is a company-level signal, not a promise about a specific req.** Amazon has GovCloud roles and Apple has US-persons-only work; the clearance title patterns catch the ones that say so in the title, but some will slip through. The embed says "Known H-1B employer", not "this role sponsors", deliberately.

Names match **exact-or-prefix** on the normalized company, so `Amazon` covers "Amazon Web Services". Prefix-anchored rather than substring because a contains-match let `Applied Materials` hit "Johns Hopkins Applied Physics Laboratory" in testing. Cleared-defense primes (Northrop, RTX, L3Harris, CACI, Leidos, Peraton, SpaceX, JHU APL…) are **deliberately absent** even though a few appear in USCIS data with small counts.

Coverage today: **~55%** of US postings match the list. The unmatched residue is dominated by exactly the employers you'd expect — SpaceX, Northrop Grumman, RTX, Johns Hopkins APL, General Dynamics, Peraton.

FY2024+ USCIS data exists only inside a Tableau dashboard with no CSV export, so FY2023 is the newest bulk source. That's acceptable here: *whether* a company sponsors is stable year over year.

## Dedupe key

`sha1(normalized_company + normalized_title + MMDDYYYY_posting_date)`

- **Date included** so the same role reposted months later is treated as genuinely new.
- **URL and source id excluded** — the same job appears in multiple repos with different tracking URLs and different UUIDs. Keying on either would notify you twice for one job. In testing this collapsed 7 cross-repo duplicates out of 68 matches.
- **`Aramente/eu-tech-jobs` is the one exception**: it keys on the source's own id instead. Its `posted_at` is not stable — 1.2% of jobs present in both the 2026-08-18 and 2026-08-24 snapshots had the date bumped forward (welcometothejungle re-dates listings it re-promotes), and under the shared key every bump mints a new id and re-notifies a job already sent, ~24/day of pure duplicates. The rule above exists for cross-source collisions, and this source has none: run against both US repos through the real filter, the overlap was exactly **0** postings. Revisit if a source is ever added that spans both regions.
- **Company suffixes, case, emoji and diacritics normalized**, so `Acme, Inc.` and `ACME` are one job.
- When a source publishes **no date**, the date the bot first saw the posting is used. Without that fallback the slot would be empty and a repost would silently collide with the original — the exact case the date is there to catch.

## First run

The first run **seeds state silently and sends nothing**. With ~33,000 postings across the sources, notifying on the backlog would mean hundreds of Discord messages.

Every run afterward notifies only on new matches. To re-seed from scratch, run the workflow with the `reseed` input checked, or `python src/main.py --reseed`.

`max_notifications_per_run` (default 60) caps a single run. If a source reformats and suddenly looks like 5,000 new jobs, you get 60 and the rest defer — not a channel flood.

**Adding a source to an already-seeded state has the same problem as a first run**, and the cap does not save you — it only spreads it out. Adding `eu-tech-jobs` put 2,671 unseen matches in front of the bot, which at 60/run drains over ~44 hourly runs. Re-seed once so the existing pool counts as old news, and use the backlog dump to review it instead of the channel:

```bash
python src/main.py --reseed          # record everything live, send nothing
python scripts/backlog_dump.py       # regenerate backlog.md to review the pool
```

Steady state after that is the true daily delta: ~73 new/day from this source, which lands in a single run just after the upstream 07:00 CET rebuild and drains over the next two.

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
scripts/render_board.py            nightly Pages board renderer
scripts/board_template.html        its CSS/JS shell (placeholders: __DATA__ etc)
scripts/backlog_dump.py            one-off catch-up dump to markdown
state/seen.json                    committed state
tests/test_core.py                 sanity checks
```
