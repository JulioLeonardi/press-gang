# Resume Tailoring: Hand-off

Status of the resume tailoring system specified in `RESUME_TAILORING.md`. The spec is the user's own, is gitignored, and is local only. Read it before this file. This file records where the build stands, the decisions behind it, and what comes next.

_Updated 2026-10-06._

## Start here

- **Branch:** `feat/resume-tailoring`, off `master`. It's pushed and open as **PR #2** (https://github.com/JulioLeonardi/press-gang/pull/2). It's independent of PR #1 (`feat/ats-polling`).
- **Done:** spec rollout steps 1–6: bank lint, render, extraction, selector, taxonomy, the user's new variants, and the local server.
- **Next:** **step 7, the Chrome MV3 extension.** The brief is below.
- **Check the build before changing anything.** Every test script should end with `all checks passed`:

  ```
  C:\Python312\python.exe tests/test_tailor_validate.py   # also: _render, _extract, _select, _measure
  C:\Python312\python.exe tests/test_server.py
  C:\Python312\python.exe tests/test_core.py
  ```

## Environment

- **Python:** use `C:\Python312\python.exe`. The default `python` is 3.14 and has no PyYAML.
- **Tests** are plain scripts with a `check()` helper, not pytest. New tests should follow the same pattern. Checks that need the gitignored bank or pdflatex skip without them.
- **Server dependencies** (fastapi, uvicorn, httpx2) are in `server/requirements.txt`, not the root `requirements.txt` that the bot's Action installs. They're already installed for 3.12.
- **websockets:** an old `websockets` package on this machine breaks uvicorn's protocol auto-detection, so `server/__main__.py` passes `ws="none"`.
- **pdflatex is MiKTeX with auto-install set to "ask".** A missing package hangs headless builds on a hidden dialog. Find the missing package with `pdflatex --disable-installer` and install it with `miktex packages install <name>`.
- **Inspecting PDFs:** `pdftotext -layout`, `pdftoppm -png` and `pdfinfo` are on PATH.
- **Backslashes:** heredocs through the Bash tool mangle backslashes, which breaks LaTeX strings. Write such scripts to a file with the Write tool.
- **Line endings:** Python on Windows writes CRLF by default. `bank.yaml` and tracked files use LF, so pass `newline="\n"` when rewriting them.

## Map

| Path | Tracked | What it is |
|---|---|---|
| `resume/bank.yaml` | **no** | The variant bank. 9 sections, 30 bullets, 37 variants, all in the `technical` voice. Seven bullets have a second variant. Lints with 0 errors and 14 warnings, all category keywords. |
| `resume/proposed.yaml` | **no** | 7 proposals, all already merged into the bank. Safe to empty. |
| `SWE Resume.tex`, `Master Resume.tex` (+ `.html`) | **no** | The user's sources. SWE is the one-page base resume; Master is the full set of bullets the bank draws from. |
| `RESUME_TAILORING.md` | **no** | The spec. |
| `.env` | **no** | `TAILOR_TOKEN` (required by the server) and `TAILOR_EXTENSION_ID` (set in step 7). Real environment variables override it. |
| `out/` | **no** | Rendered PDFs (`out/{id}/`) and `line_cache.json`. Nothing prunes it. |
| `resume/aliases.yaml`, `taxonomy.yaml`, `template.tex` | yes | Generic: no personal data. |
| `tailor/bank.py` | yes | Loaders, `alias_table`, `canonical`, `spellings`. |
| `tailor/validate.py` | yes | `python -m tailor.validate [bank]`: the bank lint. |
| `tailor/extract.py` | yes | `extract(jd, vocab, aliases, taxonomy, company)` returns a list of `Keyword(term, weight, source, evidence)`. |
| `tailor/select.py` | yes | `select(bank, keywords, aliases, cap, exclude, lines)` returns `Selection(voice, layout, score, covered, uncovered)`. `weakest_filler()` chooses the bullet to drop on overflow. |
| `tailor/measure.py` | yes | `line_counts(bank)` returns each text's rendered line count from pdflatex, cached per text. Returns `None` without pdflatex. |
| `tailor/render.py` | yes | Turns a layout into a PDF and `selection.json`. Runs the verbatim assertion and the one-page check. `python -m tailor.render` renders the untailored base. `full_layout()` is the SWE resume, used by tests. |
| `tailor/__main__.py` | yes | `python -m tailor jd.txt [company] [posting_id]`. `tailor()` extracts, selects, renders, and retries once on overflow. `explain()` formats the result. |
| `server/app.py`, `server/__main__.py` | yes | `python -m server [port]` serves on 127.0.0.1:8765. `create_app(token, extension_id, bank_path, out_dir)` is the test seam. |
| `tests/jds/` | yes | 7 real Greenhouse JDs. `expected.yaml` lists the expected terms per JD, plus a vocabulary that stands in for the bank in CI. |
| `tests/fixtures/bank/` | yes | `valid.yaml` (lint and render fixtures) and `select.yaml` (two voices and an optional section). |

## Decisions (don't reopen without new information)

### Privacy and process

- **The repo is public.** Anything holding personal data stays gitignored: the bank, the resume sources, `proposed.yaml`, `answers.yaml`, `out/` and `.env`. The step 9 Action will read the bank from a **repo secret**. Before each push, scan the branch diff for personal data such as the phone number, email or GPA.
- **Claude never edits bullet text in `bank.yaml` on its own.** It proposes variants in `resume/proposed.yaml`. The user approves them, and only then do they move into the bank, listed **after** the bullet's original wording.
- **Voices:** `technical` and `impact`. No `impact` variants exist. The spec forbids writing them in bulk, so ask the user before writing any.

### Bank content rulings from the user

- Lintito says "automated **checks**", not "tests". Its app bullet keeps the SWE wording, "gates PRs ... and design patterns".
- The RLS bullet says "110+" policies. That number grows: when it changes, update `fact`, `facts_locked` and `text` together. Do the same if "14 migrations" changes.
- SHPE Mobile App PM ran May 2025 – May 2026.
- "~400%", "(~30 devs)" and "1:1s" are correct. The user's own `.tex` renders them wrong, because in LaTeX `~` is a space and `\~` is an accent.
- All 7 merged variants were confirmed accurate (2026-10-06). That includes the wording choices: "React frontend, NestJS backend", "REST API" plus "cloud" for Modal, "continuous integration gate", and "event-driven" for the webhooks.

### Rendering and length

- **Template:** LaTeX, from the user's Jake's-template preamble. `tex()` renders `~` as `\raisebox{0.5ex}{\texttildelow}`, because `$\sim$`'s math box pushed the page to two.
- **Length is budgeted in bullet lines** (one bullet line is 14pt), measured with `\pagetotal`:

  | Element | Cost |
  |---|---|
  | Bullet | 1 |
  | Each extra wrapped line | 0.857 (12pt) |
  | Project heading | 1.44 |
  | Experience / involvement heading | 2.31 |
  | Section heading | 1.71 |

  The budget is `meta.page_lines: 37.4`, which Claude added to the bank. The SWE base uses 37.38. The header, education, skills and awards blocks are outside the budget, so editing them means re-measuring.
- **Wrapping is measured, not estimated.** `measure.py` typesets `\resumeItem`'s body verbatim; its trailing space matters at the margin. Measured counts matched all 9 drafts whose render was already known. The `LINE_CHARS = 115` character guess is used only without pdflatex, and it's unreliable between 108 and 115 characters.

### Selection

- **Section `priority`:** 1 means mandatory (the default), 3 means optional. The spec defines priority only on bullets; Claude added it to sections. Fraud Patrol and the two SHPE PM roles are priority 3.
- **Objective:** g(1)=1.0, g(2)=1.3, g(3+)=1.3, plus a priority bonus of 0.03/0.02/0.01 per bullet. The bonus uses the worse of the bullet's and section's priority. With no JD, the result is exactly the SWE resume.
- **Anti-stuffing cap is 2.** It counts only the JD's keywords. A keyword's limit rises to the number of mandatory bullets that can't avoid it; otherwise any JD mentioning Postgres would have no legal layout.
- **Ties:** voices go in `meta.voices` order and fuller layouts come first. Bullets and **variants are tried in bank order**, so the first-listed variant is the default. The user chose this over the spec's "variant id order". Variants with the same JD keywords and the same height are deduplicated to the first one listed. That's exact, and it keeps a solve at 2–9 ms.
- **Overflow:** drop the priority-3 bullet whose removal costs the least coverage, re-solve once, then error.

### Extraction

- Section weights follow the spec.
- Inline "a plus", "bonus" and "preferred" clauses are capped at 0.5.
- The employer's own name is dropped.
- All-caps spellings and spellings of 3 characters or fewer match case-sensitively ("Go", "REST", "JS").
- Taxonomy expansion is single-level, and an exact hit always beats an inferred one.

### Server

- **Routes:**
  - `GET /health` returns `{bank_version, valid, errors, warnings}`.
  - `POST /tailor` takes `{jd_text, url?, company?, title?}` and returns `{id, bank_version, score, voice, keywords, covered, uncovered, layout, pdf_url, cached}`.
  - `GET /resume/{id}.pdf` downloads as `Julio_Leonardi_Resume.pdf`.
- **The token is required on every route**, `/health` included, and is compared in constant time. CORS is open only to `chrome-extension://$TAILOR_EXTENSION_ID`, and only when that variable is set.
- **Reload:** the server checks file mtimes on each request; it doesn't use watchdog. If the bank fails the lint, `/tailor` returns 503, even for a JD it has cached.
- **Cache key:** a content hash of bank + aliases + taxonomy, the lowercased company, and the whitespace-normalized JD. The cache is in memory.
- **Errors:** no layout, or an overflow after the retry, returns 422. pdflatex runs behind a lock.
- **Timing:** about 0.75 s for a fresh JD and about 80 ms for a cached one.

## Next: step 7, the browser extension

Spec §7. Chrome MV3, loaded unpacked, never published. It's a thin client: **no selection or extraction logic in JS.**

1. **Input fallback first:** a popup with "use selected text" and a paste box. This must always work.
2. **Popup output:**
   - The score.
   - The covered keywords.
   - The top uncovered keywords, each with its `source` and `evidence`.
   - A PDF preview and download. Fetch the PDF with the token header, then open it as a blob URL.
3. **Server down:** show a clear message with the exact command, `C:\Python312\python.exe -m server`.
4. **Fixed extension id:** pin it with a manifest `key`, then put the id in `.env` as `TAILOR_EXTENSION_ID`. The token is entered once on an options page.
5. **Then the per-ATS content scripts:** Greenhouse, Lever, Ashby, Workday and SmartRecruiters, with all selectors in one config file. Reuse what the source adapters in `src/` already know about each ATS.

Open question to ask the user: should `extension/` be tracked? It holds no personal data unless the manifest `key` is considered sensitive.

## Later steps

8. `answers.yaml` (gitignored) and `POST /fields`. Return `{matched, needs_input}`. Never submit, and only autofill on a click.
9. Discord triage, **after PR #1 merges.** Postings carry no JD text today, so fetch it per matched posting: Greenhouse `?content=true`, Ashby and Lever `descriptionPlain`. Aggregator-only postings get no score. The Action runner has no pdflatex, so it either selects without rendering or installs TeX.
10. An optional LLM keyword pass behind a flag.

## Open items (none blocking)

- **Uncovered gaps with no supporting fact:** Go, Kubernetes, AWS, GCP, Azure, Terraform, Kafka, PyTorch, distributed systems and testing. These are fit signals, not something to fix in the bank.
- **Skills without a bullet:** GitHub Actions, GraphQL, NumPy and Jetpack Compose appear in the skills block, but no bullet says what they were used for. Ask the user before proposing variants for them.
- **Fixed skills block:** it's copied verbatim from the SWE resume. Making it selectable isn't in the spec.
- **Category-keyword warnings:** 14 of them, allowlisted in `meta.category_keywords`. The user may prune them.
- **Header-less boilerplate inherits the previous section's weight.** Example: Talkdesk's "Forbes Cloud 100" blurb scores "cloud" at 0.5.
- **"React JS" also counts as a JavaScript hit,** through the `JS` alias.
- **Possible taxonomy addition:** `relational databases` implying PostgreSQL, MySQL and SQL. SumUp asks for it at weight 1.0.
