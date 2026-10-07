# Resume Tailoring — Hand-off

Builds the resume tailoring system specified in `RESUME_TAILORING.md` (the user's local, gitignored spec; read it first). This file tracks progress against that spec and records the decisions made along the way.

## Resume here (updated 2026-10-06)

### Status

Branch `feat/resume-tailoring`, off `master` at `658b96c`. It is independent of PR #1 (`feat/ats-polling`). Not pushed.

| Commit | Rollout step | What |
|---|---|---|
| `d912983` | 1 | Bank lint (`tailor/validate.py`), LaTeX render path (`tailor/render.py`), `resume/template.tex` |
| `47c9bf0` | 2 + 4 | JD extraction (`tailor/extract.py`), `resume/taxonomy.yaml`, 7 real Greenhouse JDs in `tests/jds/` |
| `cbd4d5b` | 3 | Selector (`tailor/select.py`), CLI (`python -m tailor`), `tests/test_tailor_select.py` + `tests/fixtures/bank/select.yaml` |
| `770fa1b`, `c2bd679` | 3 | Measured bullet wrapping (`tailor/measure.py`); ties go to bank order |
| `618f92c` | 6 | Local server (`server/`), `tests/test_server.py` |

Done so far: rollout steps 1–6. All on PR #2 (https://github.com/JulioLeonardi/press-gang/pull/2). **Next: step 7, the browser extension** (see below). Steps 7–10 are untouched.

### Environment gotchas

- **Python:** use `C:\Python312\python.exe`. The default `python` (3.14) has no PyYAML.
- **Tests** are plain scripts, not pytest, matching `tests/test_core.py`. Run all of them:
  `python tests/test_tailor_validate.py`, `test_tailor_render.py`, `test_tailor_extract.py`, `test_tailor_select.py`, `test_tailor_measure.py`, `test_server.py`, `test_core.py`.
- **Server deps** are in `server/requirements.txt` (fastapi, uvicorn, httpx2), not the root `requirements.txt` the bot's Action installs. An old `websockets` package on this machine breaks uvicorn's auto-detection, so `server/__main__.py` passes `ws="none"`.
- **PDF tooling:** `pdflatex` is MiKTeX. Its auto-install setting is "ask", which hangs headless builds on a hidden dialog. If a package is missing, find it with `pdflatex --disable-installer` and install it with `miktex packages install <name>`. titlesec, marvosym and fancyhdr are already installed.
- **Checking output:** `pdftotext -layout` and `pdftoppm -png` are on PATH. Compare a render with your PDF viewer or by diffing the extracted text.
- **Shell quoting:** heredocs through the Bash tool mangle backslashes, which matters for LaTeX strings. Write such scripts to a file first.

### Where things live

| Path | Tracked | Notes |
|---|---|---|
| `resume/bank.yaml` | **no** (personal) | 13 sections, 30 variants, all `technical` voice. Validates with 0 errors and 10 category-keyword warnings. The user approved its contents ("lgtm"). |
| `SWE Resume.tex` / `Master Resume.tex` (+ `.html`) | **no** | The user's sources. SWE is the one-page target; Master is the bank's universe. |
| `RESUME_TAILORING.md` | **no** | The spec. |
| `resume/aliases.yaml`, `resume/taxonomy.yaml`, `resume/template.tex` | yes | Generic, no personal data. |
| `tailor/bank.py` | yes | Loaders, `alias_table`, `canonical`, `spellings`. |
| `tailor/validate.py` | yes | `python -m tailor.validate`. Also accepts a keyword if the section's `title`/`stack` states it, or an inflected form does (stem match). |
| `tailor/render.py` | yes | `python -m tailor.render [posting_id]` renders the untailored base (selector with no keywords) to `out/{id}/Julio_Leonardi_Resume.pdf` + `selection.json`. `full_layout()` (every priority-1 section, all bullets) is the SWE resume; tests compare against it. |
| `tailor/select.py` | yes | `select(bank, keywords, aliases, cap, exclude)` → `Selection(voice, layout, score, covered, uncovered)`. Branch-and-bound per voice, ~2 ms on the real bank. `weakest_filler()` picks the bullet to drop on overflow. |
| `tailor/measure.py` | yes | `line_counts(bank)` → text → rendered line count, measured by pdflatex and cached in `out/line_cache.json`. Returns None without pdflatex. |
| `tailor/__main__.py` | yes | `python -m tailor jd.txt [company] [posting_id]`: extract → select → render (one overflow retry) → explanation. |
| `server/app.py`, `server/__main__.py` | yes | `python -m server [port]` on 127.0.0.1:8765. `create_app(token, extension_id, bank_path, out_dir)` is the test seam. |
| `.env` | **no** | `TAILOR_TOKEN` (required) and `TAILOR_EXTENSION_ID` (pins CORS once step 7 fixes the id). The real environment overrides it. |
| `tailor/extract.py` | yes | `python -m tailor.extract jd.txt [company]`. `extract(jd, vocab, aliases, taxonomy, company)` returns `Keyword(term, weight, source, evidence)`. |
| `tests/jds/expected.yaml` | yes | Expected terms per JD, plus a vocabulary list that stands in for the gitignored bank's keywords in CI. |

### Decisions already made (don't re-open without new data)

- **The repo is public.** The bank, the resume sources, `proposed.yaml`, `answers.yaml` and `out/` are gitignored. The Action (step 9) will read the bank from a **repo secret**.
- **Voices:** `technical` + `impact`. Only `technical` variants exist so far. `impact` gets written in step 5.
- **Anti-stuffing cap:** 2.
- **Bank content rulings from the user:**
  - Lintito says "automated **checks**" (not "tests"), and its app bullet keeps the SWE wording ("gates PRs ... and design patterns").
  - RLS bullet: "110+" policies. The number grows; update `fact`, `facts_locked` and `text` together, and do the same if "14 migrations" changes.
  - Header email: the gmail address from the SWE resume.
  - SHPE Mobile App PM: May 2025 – May 2026.
  - "~400%", "(~30 devs)" and "1:1s" are correct. The user's own `.tex` renders them wrong: in LaTeX, `~` is a space and `\~` is an accent.
- **Rendering:** LaTeX from the user's own Jake's-template preamble. `tex()` renders `~` as `\raisebox{0.5ex}{\texttildelow}`. `$\sim$` looks the same, but its math box pushed the page to 2.
- **Section `priority`** (1 = always include, 3 = optional filler) is a field I added to sections. The spec only defines it on bullets. The four entries that exist only in the Master resume (Fraud Patrol, the two SHPE PM roles) are priority 3.
- **Extraction behavior:**
  - Section weights follow the spec.
  - Inline "a plus" / "bonus" / "preferred" clauses are capped at 0.5.
  - The employer's own name is dropped from exact hits.
  - All-caps and ≤3-character spellings match case-sensitively ("Go", "REST", "JS").
  - Expansion is single-level. An exact hit always beats an inferred one.

### Selector decisions (step 3)

- **Length model:** costs measured with `\pagetotal` on the template, in bullet lines (14pt): bullet 1 (2 if over 115 chars), project heading 1.44, experience/involvement heading 2.31, section heading ("Projects") 1.71. The budget is `meta.page_lines: 37.4` in the bank, which I added; the SWE base uses 37.38. The fixed blocks (header, education, skills, awards) are outside the budget, so editing them means re-measuring. The method is in the selector's commit message. Every test JD's first-try layout renders to one page with 8–14pt to spare.
- **Anti-stuffing cap applies only to the JD's keywords**, and a keyword's limit rises to the number of mandatory bullets that can't avoid it. **Raise this with the user.** Without that rule, any JD that mentions Postgres has no legal layout: usp_rls, usp_stripe and lintito_deploy are all priority 1 with one variant each. Side effect: when a JD says "machine learning", teebot_anomaly is dropped, because the two forced TeeBot bullets already hold the term. The user may prefer to retag, or to add variants without the term.
- **Priority bonus** 0.03/0.02/0.01 for effective priority 1/2/3, where effective = the worse of bullet and section priority. With no JD this reproduces the SWE base exactly, and a swap must gain more coverage than the bonus it gives up.
- **Section `priority` 1 means mandatory.** It defaults to 1 when absent. Optional sections can be left out entirely.
- **Overflow fallback:** drop the selected effective-priority-3 bullet whose removal costs the least coverage, re-solve once, then error. Tested with a stubbed render. The real bank hasn't triggered it.
- **Ties:** voices in `meta.voices` order; options with the fullest set of bullets first, bullets in bank order, variants in bank order; only a strictly better score replaces the incumbent. The user chose bank order over the spec's "variant id order" (2026-10-06): the first-listed variant (the SWE wording) is the default, and a new wording appears only when it covers more.

### Server decisions (step 6)

- **Routes:** `GET /health` returns bank version, lint status and warning count. `POST /tailor` takes `{jd_text, url?, company?, title?}` and returns `{id, bank_version, score, voice, keywords, covered, uncovered, layout, pdf_url, cached}`. `GET /resume/{id}.pdf` downloads as `<Name>_Resume.pdf`. `/fields` waits for step 8.
- **Every route needs `X-Tailor-Token`, `/health` included,** compared in constant time. The popup can fetch the PDF with the header and open it as a blob URL.
- **Reload checks file mtimes on each request instead of using watchdog.** Same behavior: no thread, no dependency. If the lint fails, `/tailor` returns 503, even for a JD it already cached.
- **Cache key:** the content hash of bank + aliases + taxonomy, the lowercased company, and the whitespace-normalized JD. The cache lives in memory; PDFs go to `out/{id}/`. Nothing prunes `out/`.
- **Errors:** `NoLayout` or a second overflow → 422. pdflatex runs one at a time behind a lock.
- **Live smoke test:** 401 without a token. The SumUp JD took 755 ms the first time (render included) and 78 ms from cache. One-page PDF. Listening on 127.0.0.1 only.

### Next: step 7

- **Step 5 (user-led):** the bank has one variant per bullet, so the selector only chooses bullets and sections today. Variants proposed into `resume/proposed.yaml` (never `bank.yaml`) would give it real choices. The uncovered lists from `python -m tailor tests/jds/*.txt` show what's missing. Recurring gaps: Go, JavaScript/frontend, distributed systems, CI/CD, testing, cloud. Only propose variants that make claims the user's facts support.
- **Step 5 status (2026-10-06):** the user confirmed all 7 proposed technical variants as accurate, and they're now in `bank.yaml`, listed after each bullet's original. With all 7 merged, the lint is clean, every test JD scores higher and every one renders to 1 page. Gaps no fact supports: Go, Kubernetes, AWS, GCP, Azure, Terraform, Kafka, PyTorch, distributed systems, testing. GitHub Actions, GraphQL, NumPy and Jetpack Compose are in the skills block but no bullet says what they were used for; ask the user before writing variants for them. No `impact` variants yet: the spec says not to write them in bulk.
- **Wrapping is measured, not guessed.** `tailor/measure.py` typesets each variant in the template and reads its line count; a wrapped line costs 12pt (0.857 bullet lines). Counts are cached per text in `out/line_cache.json`, so a new wording costs one ~0.6 s pdflatex run. Without pdflatex, `select` falls back to `LINE_CHARS`, which is unreliable at 108–115 chars. With 4 wrapping drafts added to the bank, the character guess overflowed on all 7 JDs; measured counts fit all 7 and still used the wrapped bullets.
- **Step 7:** MV3 extension. Build the paste box and "use selected text" first, then the per-ATS extractors. Pin the id with a manifest `key`, then set `TAILOR_EXTENSION_ID`. The token goes in the options page. Fetch the PDF with the token header and open it as a blob. When the server is down, show `python -m server`.

### Later steps (spec rollout)

5. The user writes more variants. Claude may only propose them, into `resume/proposed.yaml`.
6. FastAPI server on 127.0.0.1 with an `X-Tailor-Token` header, CORS pinned to the extension origin, and a watchdog reload.
7. MV3 extension: paste/selection fallback first. Pin the extension id with a manifest `key`; the token goes in an options page.
8. `answers.yaml` + `/fields`.
9. Discord triage, **after PR #1 merges**. Postings carry no JD text today, so fetch it per matched posting (Greenhouse `?content=true`, Ashby/Lever `descriptionPlain`). Aggregator-only postings get no score.
10. Optional LLM keyword pass behind a flag.

### Open items (none blocking)

- **Category-keyword warnings:** 10 remain (security, SQL, payments, …). They're allowlisted in `meta.category_keywords`; the user may prune them.
- **Header-less boilerplate inherits the preceding section's weight.** Talkdesk's "Forbes Cloud 100" blurb, sitting after its Nice To Have list, scores "cloud" at 0.5. It's weak noise.
- **"React JS" also counts as a JavaScript hit** (via the `JS` alias). Harmless for now.
- **The skills block is fixed text** copied from the SWE resume. Making skill items selectable isn't in the spec yet.
