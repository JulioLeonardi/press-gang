# Direct ATS Polling — Implementation Plan

Adds direct polling of company applicant-tracking-system (ATS) boards (Greenhouse, Ashby, Recruitee, and later others) to the existing pipeline, without double-notifying roles that Aramente/eu-tech-jobs already delivers.

This replaces the phased rollout in `SOURCES.md` (a local, gitignored design note). That doc is written as though the bot were a blank slate. Most of its infrastructure already exists here, and its dedupe proposal conflicts with measured decisions in [src/fetch.py](src/fetch.py).

## Resume here (updated 2026-09-14)

### Status

Branch `feat/ats-polling`, not pushed or merged. Three commits on top of `master` at `658b96c`:

| Commit | What |
|---|---|
| `2ad8304` | Phase 1: `ats_key` dedupe, silent per-group seeding |
| `bc9059e` | Phases 0 + 2: `src/ats.py`, 20 boards in `config/companies.yaml`, coverage script |
| `f8c6512` | Company boards EU-only; 24 new title exclusions |

- `origin/master` is 79 commits ahead, all bot `chore: update seen.json`, touching only `state/seen.json`. This branch never touches that file, so the merge is clean.
- The local `state/seen.json` is stale (2026-08-26). Any dry run against it reports ~1,100 "new" postings. That's expected and not a regression.
- Tests pass: `python tests/test_core.py`.

### Next steps, in order

1. **Sync:** `git fetch origin && git merge origin/master` on the branch, re-run the tests and a dry run.
2. **Ship:** push the branch, open a PR, merge to `master`. Only the user decides this; it hasn't been approved yet. What the first production run does:
   - Marks the four configured source names in `seeded_groups`.
   - Records each company board's current matches silently. Look for `N from groups seen for the first time, recorded silently`. There should be no Discord burst.
   - Heartbeat excludes those `seeded` entries from "new this week".
3. **Watch the channel for ~3 days.**
   - Company-board roles should be only roles posted after the merge.
   - If a role arrives twice, compare `ats_key(url)` for both links. A keyless one (Welcome to the Jungle, a custom careers page without `gh_jid`) is the known gap.

### Open items (none blocking)

- **Borderline titles still passing:** "Finance Systems Specialist", "Compliance Quality Assurance Specialist", "Salesforce Consultant", "AI Talent Pool". They were left in deliberately.
- **Senior-title guard misses "Distinguished Engineer".** `senior_title_patterns` is shared with Aramente, so probe it the same way before adding.
- **`scripts/backlog_dump.py` dedupes by id only.** It's a manual report, so this is cosmetic.
- **Doctolib is mid-migration** (Greenhouse and Ashby both live). If `greenhouse/doctolib` starts 404ing, don't just switch to Ashby: Aramente keys Doctolib by Greenhouse id, so every role would arrive twice until Aramente follows. Re-run `scripts/ats_coverage.py` first.
- **Deferred:** SmartRecruiters, Recruitee and Personio adapters (no candidate needs them), notification batching, and pruning latency-only companies after a month.

### Decisions already made (don't re-open without new data)

- **No company + title dedupe fallback.** It would merge distinct keyless reqs, e.g. Amazon's.
- **No custom adapters for big-tech careers sites.** The US repos and Aramente already carry them.
- **Company boards are EU-only.**
- **Every new filter term is probed against the post-filter matches of all sources, and every removed title is read.** Bare words that hit CS roles are rejected, documented in the config, and pinned by a keep-test.

### How to verify things

```bash
python tests/test_core.py                  # all checks
python src/main.py --dry-run               # full pipeline, sends nothing, writes nothing
python scripts/ats_coverage.py             # per-board EU matches vs Aramente coverage
```

- On Windows, prefix with `PYTHONIOENCODING=utf-8` when printing titles. The console codepage chokes on non-ASCII, and the dry-run log shows a harmless `UnicodeEncodeError` otherwise.
- To compare old and new behaviour exactly, cache one fetch to JSON and run `main()` with `fetch_source` and `notify` monkeypatched, instead of doing two live runs.

### Where things live

| Location | What |
|---|---|
| `src/normalize.py` | `ats_key(url)` |
| `src/main.py` | `split_new`, `record`, `mark_seeded`; `seeded_groups` migration |
| `src/ats.py` | Board parsers, `screen_titles`, `eu_only` |
| `src/filter.py` | `eu_only` handling |
| `config/companies.yaml` | Polled boards |
| `scripts/ats_candidates.yaml` | Every candidate, plus why the rejected ones were rejected |
| `config/sources.yaml` | `ats` source, shared title-list anchors, new exclusions with their traps |
| `tests/fixtures/` | Trimmed real Greenhouse/Ashby/Lever responses |

Everything below is the original plan, annotated with results as phases finished.

## What we measured (2026-09-14)

These numbers drive every decision below. Re-run the Phase 0 script before trusting them months from now.

**Aramente already sources from these ATSs.** URL hosts across its 19,611 live rows:

| Host | Rows |
|---|---|
| Workday | 4,173 |
| Greenhouse | 3,911 |
| Welcome to the Jungle | 3,325 |
| other | 3,000 |
| SmartRecruiters | 1,923 |
| Ashby | 1,597 |
| Lever | 859 |
| Personio | 727 |
| Recruitee | 96 |

**For companies Aramente tracks, it already has every EU role.** Live boards compared against the snapshot, with locations classified by our own `LocationMatcher`:

| Board | Live jobs | EU jobs | EU jobs missing from Aramente |
|---|---|---|---|
| greenhouse/adyen | 223 | 77 | **0** (all 136 missing are SF, Chicago, NYC, Singapore…) |
| greenhouse/gitlab | 225 | 22 | **0** (missing are US/Canada remote, Bangalore) |
| ashby/mollie | 49 | — | **1** of 49 |
| greenhouse/n26 | 73 | 73 | 0 by title (73/73), but **0 by URL**: see below |
| recruitee/bunq | — | — | **all**: Aramente doesn't track bunq |

**Consequences:**

1. Polling an Aramente-covered company directly buys **latency, not coverage**. Aramente rebuilds daily (~07:00 CET); we'd see new roles within the hour.
2. Real coverage gains come only from **companies Aramente doesn't track** (bunq-type cases).
3. The same job reaches us through **different URLs**. N26's Aramente link is `n26.com/en-eu/careers/positions/8184721?gh_jid=8184721`; Greenhouse's is `job-boards.greenhouse.io/n26/jobs/8184721`. Both carry the Greenhouse job ID, so dedupe can match on the ID instead of on company + title.

## Design decisions

### 1. Cross-source dedupe on a canonical ATS key

A new pure function `ats_key(url) -> str | None` in [src/normalize.py](src/normalize.py) extracts the ATS's own job identifier:

| Pattern | Key |
|---|---|
| `greenhouse.io/{board}/jobs/{id}`, or any URL with `gh_jid={id}` | `gh:{id}` |
| `jobs.ashbyhq.com/{board}/{uuid}` | `ashby:{uuid}` |
| `jobs.lever.co/{company}/{uuid}` | `lever:{uuid}` |
| `smartrecruiters.com/.../postings/{id}` | `sr:{id}` |
| `{company}.recruitee.com/o/{slug}` | `recruitee:{company}/{slug}` |
| `{company}.jobs.personio.{de,com}/job/{id}` | `personio:{id}` |

Greenhouse, Ashby, Lever and SmartRecruiters IDs are globally unique, so the board name stays out of the key. Recruitee slugs are only unique per company, so the company stays in.

Every posting from every adapter gets an `ats_key` field (`None` when no pattern matches). The existing `id` is **unchanged** for current sources, so `_EU_ID_NOTE` and the repost/date logic stay intact.

**Seen check** (`split_new` in [src/main.py](src/main.py)). A posting is already seen if its `id` **or** its `ats_key` is in state, or appeared earlier in the same run.

**No company + title fallback.** An earlier draft added one: a `role_key` match within 45 days. It was dropped during Phase 1 because it would regress existing sources. `amazon.jobs` URLs carry no ATS id, and Amazon posts many distinct reqs under one title, so every req after the first would be silently dropped. It isn't needed for the rollout either:

- Silent per-group seeding (below) covers roles Aramente sent before keys were recorded.
- Since Phase 1, every notification stores its key.

The remaining gap is a company we poll directly that Aramente links through a keyless URL (e.g. Welcome to the Jungle). Phase 0 measures that per company before it's added.

**State shape.** Entries gain optional `ats_key` and `seeded` fields; state gains `seeded_groups`:

```json
"8f3a...": {"first_seen": "...", "company": "...", "title": "...",
            "ats_key": "gh:8184721"}
```

Measured on the 2026-09-14 fetch:

- Key coverage is ~2/3 of Aramente postings and ~1/3 of SimplifyJobs postings.
- 74 keys were already shared by two ids across the existing sources, i.e. real duplicates today. None had both ids notified on that data.

### 2. Per-group silent seeding

The current bootstrap is global: the first run seeds everything silently. Without per-group seeding, adding one company to `companies.yaml` would notify its entire open backlog (capped at 60, and deferred across runs).

Each posting gets a `seed_group`: `"ats/greenhouse/adyen"` for direct polls, and the source name for existing sources. State gains `"seeded_groups": [...]`. In `main.py`, new matches whose group isn't in `seeded_groups` are recorded without notifying, and the group is then marked seeded.

Existing sources are written into `seeded_groups` on first load, so they keep notifying normally.

Known edge case: a board with zero open roles when added is never seeded, so its first real posting gets seeded silently instead of notified. That's rare for target companies and not worth a success/empty signal through `fetch_source`.

### 3. One `ats` adapter reading a company registry

- **Code:** a new module, [src/ats.py](src/ats.py), holds one parser per ATS, dispatched from `fetch.py`'s adapter table as `adapter: ats`.
- **Config:** `config/companies.yaml`, kept separate from `sources.yaml` because it will grow:

  ```yaml
  companies:
    - {name: bunq,   ats: recruitee,  board: bunq}
    - {name: Adyen,  ats: greenhouse, board: adyen}
    - {name: Mollie, ats: ashby,      board: mollie}
  ```

- **Concurrency:** `ThreadPoolExecutor(max_workers=8)` over companies. It stays on `requests`; no `httpx` rewrite.
- **Failure isolation:** failures are isolated per company, not just per source. A 404 logs at WARNING with the company name, so a moved board gets noticed.
- **Schedule:** unchanged at hourly. That's one request per company per hour, and each run adds roughly 10 seconds.

Posting dict, matching the existing shape:

| Field | Greenhouse | Ashby | Recruitee |
|---|---|---|---|
| endpoint | `boards-api.greenhouse.io/v1/boards/{b}/jobs` (no `content=true`: we don't use descriptions) | `api.ashbyhq.com/posting-api/job-board/{b}` | `{b}.recruitee.com/api/offers/` |
| `id` | `sha1(ats_key)[:16]` | same | same |
| `title` | `title` | `title` | `title` |
| `location` | `location.name` | `location` + `secondaryLocations[].location`, joined `" \| "` | `locations[]` as `"city, country"`, joined `" \| "` |
| `url` | `absolute_url` | `jobUrl` | `careers_url` |
| `date_posted` | `first_published` (**not** `updated_at`, which bumps) | `publishedAt` | `published_at`: strip the trailing `" UTC"` first, since `fromisoformat` rejects it |
| skip when | — | `isListed` is false | `status != "published"` |
| `company` | from `companies.yaml` | same | same |
| `sponsorship_flag` | `"unknown"` | same | same |
| `source_repo` | `"ats/greenhouse"` | `"ats/ashby"` | `"ats/recruitee"` |

`source_repo` uses a `/` so [scripts/render_board.py](scripts/render_board.py) (which takes `split("/")[-1]`) shows the ATS name.

IDs come from the ATS key rather than `make_id(company, title, date)` for the same reason `_EU_ID_NOTE` gives: the key is stable, and cross-source matching now happens through `ats_key`.

### 4. Share the CS-title screen with Aramente

ATS boards list every role a company has (Adyen's includes "Account Manager, Kuala Lumpur"). They need the same positive title screen and senior-title guard the Aramente source already uses. Those options are currently tied to `eu_rows_to_postings`.

- Lift `_compile_required_title` / `_compile_senior_title` usage into a small `screen_titles(postings, options) -> (kept, dropped_counts)` in `fetch.py`, and call it from both adapters.
- In `sources.yaml`, share the lists with YAML anchors, which `yaml.safe_load` supports, so they can't drift apart:

  ```yaml
  require_title_patterns: &cs_titles
    - software
    ...
  # later, under the ats source:
  require_title_patterns: *cs_titles
  senior_title_patterns: *senior_titles
  ```

The Aramente-only options (`role_families`, `exclude_seniority`, `exclude_industry_tags`) stay on that source.

## Phases

Each phase ends in a state that can ship on its own.

### Phase 0: coverage script (decides the company list) — **done**

**Results (2026-09-14)**, starting from the 46 companies in `companies_seed_list.md`:

- **No public board:** 15 companies, including all the big-tech names, Klarna, Revolut, Zalando and Booking.com. No custom adapter was written: those scrapers would mean fighting search sites, and SimplifyJobs and Aramente already carry these companies.
- **Same-name impostors:** 6 probe hits belonged to other companies (Google's was a sample board, Meta's Addis Ababa University, Uber's a "Test UAT" job) and were excluded. Wise's Greenhouse board is US field sales.
- **Verified boards:** 26, ranked in `scripts/ats_candidates.yaml`. Real EU CS gain was ~50 open roles, mostly Feedzai (10), Grafana (6), Cabify (5) and SumUp (4).
- **Doctolib:** it is on both Greenhouse and Ashby under different job ids. Only the Greenhouse board is safe to poll, because Aramente keys Doctolib by Greenhouse id.
- **Recruitee and SmartRecruiters:** no parsers. bunq, the only Recruitee candidate, had 0 EU CS roles. The two SmartRecruiters companies (Wise, Delivery Hero) are already covered, and Wise has 258 keyless WTTJ links in Aramente.

`config/companies.yaml` holds the 20 boards with at least one EU match.

The original Phase 0 spec follows.

`scripts/ats_coverage.py`, run locally and never in Actions:

- `detect_ats(careers_url) -> (ats, board) | None`: parses the patterns above plus `boards.greenhouse.io/{b}`, `job-boards.greenhouse.io/{b}` and `?gh_jid=`. It's used to add companies by pasting a URL.
- For each candidate company, it prints live EU CS jobs (after `LocationMatcher` and the title screen), how many are in the Aramente snapshot (by `ats_key`, then `role_key`), and the Aramente lag for the five newest roles.
- Input is a plain text file of careers URLs; output is a table.

**Exit:** a ranked candidate list in two buckets:

- **gap:** Aramente is missing EU roles.
- **latency:** fully covered, but worth hearing about hourly.

Seed `companies.yaml` with every gap company and at most about 15 latency companies.

### Phase 1: dedupe keys and per-group seeding (no new sources) — **done**

- `ats_key()` in `normalize.py`.
- `fetch_source` sets `ats_key` and `seed_group` on every posting, unless the adapter already did.
- `main.py`:
  - `split_new` (id-or-key seen check, key collapse within a run, seeded/unseeded split)
  - `record`
  - `mark_seeded`
  - migration of `seeded_groups` from the configured source names
- `render_board.py` collapses on `ats_key`; `heartbeat.py` skips `seeded` entries.
- Tests.

**Exit (met):** `main()` on one cached live fetch (23,684 postings) produced an identical notification list before and after: 1,116 ids. A real-write simulation on a copy of `seen.json` confirmed four things:

- Existing sources migrate as seeded.
- A new board's matches are recorded silently with `seeded: true`.
- A duplicate of an already-notified key is dropped.
- The board's next posting notifies.

`scripts/backlog_dump.py` still dedupes by id only. It's a manual catch-up report, so it was left alone.

### Phase 2: Greenhouse, Ashby and Lever adapters — **done**

Built: `src/ats.py` (Lever joined because two gap companies use it), an `adapter: ats` source, YAML anchors, three fixtures, and adapter tests.

`screen_titles` lives in `ats.py` rather than being extracted from `eu_rows_to_postings`. Aramente's seniority-tag logic doesn't apply to ATS boards, and extracting it would have meant refactoring working code.

**Exit (met):** a live dry run fetched 3,686 postings from the 20 boards. The title screen kept 502, and 121 matched after dedupe. All 121 were recorded silently, and the notify list was identical to the run without the source.

**Follow-ups — done:**

- **US matches → EU-only.** The source was adding ~70 US known-sponsor matches (30 from Stripe), mostly not new-grad. ATS postings now carry `eu_only`, and `filter_postings` skips their US location parts, so a "Seattle | Berlin" role still matches on Berlin.
- **Title noise → 24 new exclusions** in `exclude_title_patterns`: `representative`, plus phrases like `fleet development`, `support agent` and `financial controller`.
  - Each term was probed against the full match pool of all four sources. Together they remove 55 of 2,666 matches, all non-CS: 29 company-board, 26 from the other three sources.
  - Bare words rejected as traps are recorded in the config and guarded by tests: `specialist`, `consultant`, `agent`, `commerce`, `strategy`, `controller` and others.
  - With both changes, the company boards yield 136 EU matches, down from 223.

The original Phase 2 spec follows.

- `src/ats.py` with those two parsers, `screen_titles` extraction, `adapter: ats` in the dispatch, and anchors in `sources.yaml`.
- `companies.yaml` with the Phase 0 companies on those two ATSs.
- Fixtures: one trimmed real response per ATS in `tests/fixtures/` (3–5 jobs each, including a non-EU job, an `isListed: false` job, and a non-CS title).

**Exit:** the dry run shows each new group seeding silently. A second dry run, or one against a state file with those groups pre-seeded, shows zero ATS notifications for roles Aramente already sent. Then ship and watch the channel for 3 days.

### Phase 3: Recruitee, plus others only if Phase 0 found gaps

- Recruitee parser (the confirmed bunq gap).
- Lever, SmartRecruiters and Personio **only** if Phase 0 found gap companies on them. Their caveats:
  - Lever and SmartRecruiters both return `200` with an empty list for some unknown or empty boards (`lever/mistral` → `[]`, SmartRecruiters for a nonexistent company → `totalFound: 0`). Log an empty board at INFO on every run, so a dead slug is visible.
  - SmartRecruiters paginates (`limit`/`offset`).
  - `celonis.jobs.personio.de/xml` returned a 307. Aramente's URLs use `.jobs.personio.com`, so check the current feed URL before writing that parser.
- Grow `companies.yaml` toward 40.

### Deferred: decide after a month of Phase 2–3 data

- Notification batching and grouping by country. Only needed if the volume actually hurts. Direct polling of Aramente-covered companies adds almost no *new* notifications, only earlier ones.
- Dropping latency-bucket companies that never produced a role you acted on.

## Tests

Added to [tests/test_core.py](tests/test_core.py) in its existing `check(label, actual, expected)` style.

**`ats_key`:**
- Both Greenhouse hosts
- The N26 `?gh_jid=` careers URL, which must equal the key from the Greenhouse URL
- Ashby, Lever, SmartRecruiters API URL, Recruitee subdomain
- Personio `.de` and `.com`
- A Workday URL → `None`

**Dedupe:**
- An Aramente posting and a Greenhouse posting with the same `gh:` key → one notification.
- Same title, different real `ats_key`s → **two** notifications (the Adyen identical-title case).
- Keyless postings with different ids → two notifications (the Amazon case).
- A state entry with a key, plus a new posting with that key under another id → seen.

**Seeding:**
- An unseeded group's matches are recorded without calling `notify`.
- An already-seeded group notifies.
- Existing sources are auto-marked seeded on load.

**Adapters, from fixtures:**
- Field mapping per ATS
- `isListed: false` / unpublished skipped
- `first_published` used over `updated_at`
- Recruitee `" UTC"` date parses
- Multi-location join uses `" | "` so `filter_postings` splits it

**`screen_titles`:** the existing Aramente title-screen tests keep passing once the function is extracted.

## Explicitly not doing

These are proposed in SOURCES.md and cut here.

- **`Source` ABC / class refactor.** The adapter table in `fetch.py` already provides this.
- **Changing existing IDs to `hash(company, title, country)`.** That conflicts with `_EU_ID_NOTE`'s measurements. `ats_key` solves cross-source matching without touching IDs.
- **Aggregators, RSS boards, national portals.** There's no evidence yet that they cover anything Aramente doesn't.
- **Tri-state `remote`, a `Posting` dataclass, storing descriptions.** None of these is needed for the goal, and descriptions would inflate `seen.json`.
- **Replacing the H-1B sponsor logic** with "US and mentions sponsorship". That would be a regression.
- **`asyncio`/`httpx`.**

## Open questions

1. **Latency vs. noise.** Is hearing about a role at an Aramente-covered company about 12 hours earlier worth maintaining those registry entries? Phase 0's lag column should answer this; if typical lag is under a day, keep the latency bucket small.
2. **Keyless duplicates.** A directly polled company that Aramente links through a keyless URL would notify twice. Phase 0 should count these per candidate. If they're common for a company, either skip it or revisit a narrow fallback scoped to that company's `seed_group`, never a global one.
3. **Heartbeat inflation.** [src/heartbeat.py](src/heartbeat.py) has no per-source logic. It counts `state["postings"]` entries by `first_seen`, so a group's silent seeding shows up as a spike in "new this week". Either mark seeded entries (`"seeded": true`) and have heartbeat skip them, or accept a one-week blip whenever companies are added. Marking them is about three lines and is the better default.
