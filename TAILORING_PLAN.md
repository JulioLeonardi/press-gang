# Resume Tailoring — Hand-off

Builds the resume tailoring system specified in `RESUME_TAILORING.md` (the user's local, gitignored spec; read it first). This file tracks progress against that spec and records the decisions made along the way.

## Resume here (updated 2026-10-06)

### Status

Branch `feat/resume-tailoring`, off `master` at `658b96c`. It is independent of PR #1 (`feat/ats-polling`). Not pushed.

| Commit | Rollout step | What |
|---|---|---|
| `d912983` | 1 | Bank lint (`tailor/validate.py`), LaTeX render path (`tailor/render.py`), `resume/template.tex` |
| `47c9bf0` | 2 + 4 | JD extraction (`tailor/extract.py`), `resume/taxonomy.yaml`, 7 real Greenhouse JDs in `tests/jds/` |

Done so far: rollout steps 1, 2 and 4. **Next: step 3, the selector** (see below). Steps 5–10 are untouched.

### Environment gotchas

- **Python:** use `C:\Python312\python.exe`. The default `python` (3.14) has no PyYAML.
- **Tests** are plain scripts, not pytest, matching `tests/test_core.py`. Run all of them:
  `python tests/test_tailor_validate.py`, `test_tailor_render.py`, `test_tailor_extract.py`, `test_core.py`.
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
| `tailor/render.py` | yes | `python -m tailor.render [posting_id]` writes `out/{id}/Julio_Leonardi_Resume.pdf` + `selection.json`. `full_layout()` is a **stand-in** for the selector: every priority-1 section, all bullets, technical voice. That reproduces the SWE resume exactly. |
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

### Next: step 3, the selector

Build `tailor/select.py` and `tailor/__main__.py` (`python -m tailor jd.txt [company]` → PDF + explanation), then swap `full_layout()` out of `render.main`.

- **The page has zero slack.** The SWE base fills one page exactly. Bullets run 94–112 characters and each renders on one line. Adding any bullet or entry overflows, and so does including priority-3 entries; `test_tailor_render.py` relies on that. The length model therefore has to budget lines: one per bullet, plus heading overhead per section entry. Verify by rendering, and on 2 pages drop the lowest-scoring priority-3 bullet and re-solve, per the spec.
- **Voices:** solve per voice. Skip a voice that can't fill the sections it would include; `impact` currently covers nothing.
- **Objective and constraints** come from spec §3: g(1)=1.0, g(2)=1.3, g(3+)=1.3, a priority bonus, the cap of 2 per keyword, section `min_bullets`/`max_bullets`, and priority-1 bullets wherever their section is included. It must be deterministic (tie-break on variant id order) and run in under 200 ms.
- **Solver:** the plan was `pulp` (bundled CBC solver), with a tiny ε term for deterministic tie-breaks. With only 30 variants and one voice today, exhaustive search with pruning may be simpler. Measure before adding the dependency.
- **Explanation output:** covered keywords with the variant covering each, uncovered keywords sorted by weight with `source`/`evidence`, and the score.
- **Tests (spec "Testing"):** on every `tests/jds` JD, assert the constraints hold, run twice for identical output, check runtime. They need a fixture bank, since the real one is absent in CI. Extend `tests/fixtures/bank/valid.yaml` or add a second fixture.

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
