"""Selection: constraints, voice choice, the anti-stuffing cap, determinism, speed.

Run: python tests/test_tailor_select.py
The real-bank checks need resume/bank.yaml (gitignored) and pdflatex; they
skip when either is missing.
"""

import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tailor.bank import ALIASES_PATH, BANK_PATH, alias_table, canonical, load_yaml
from tailor.extract import TAXONOMY_PATH, Keyword, extract, vocabulary
from tailor.render import full_layout, render
from tailor.select import CAP, ENTRY_LINES, HEADING_LINES, LINE_CHARS, select, weakest_filler
from tailor.validate import validate

ALIASES = load_yaml(ALIASES_PATH)
TABLE = alias_table(ALIASES)
TAXONOMY = load_yaml(TAXONOMY_PATH)
EXPECTED = load_yaml(ROOT / "tests" / "jds" / "expected.yaml")
FIXTURE = load_yaml(ROOT / "tests" / "fixtures" / "bank" / "select.yaml")
failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}, got {actual!r}")
        failures.append(label)


def _raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


def kw(**weights):
    return [Keyword(term.replace("_", " "), w, "exact", "") for term, w in weights.items()]


def chosen(selection):
    return [b["variant_id"] for e in selection.layout for b in e["bullets"]]


def violations(bank, selection, keywords):
    """Every hard constraint the selection breaks, as readable strings."""
    sections = {s["id"]: s for s in bank["sections"]}
    variants = {v["id"]: (b, v) for s in bank["sections"] for b in s["bullets"] for v in b["variants"]}
    found, held, lines, kinds = [], Counter(), 0.0, set()
    for entry in selection.layout:
        section = sections[entry["section_id"]]
        ids = [b["variant_id"] for b in entry["bullets"]]
        picked = [variants[i] for i in ids]
        bullet_ids = [b["id"] for b, _ in picked]
        if len(set(bullet_ids)) != len(bullet_ids):
            found.append(f"{section['id']}: a bullet appears twice")
        if not section.get("min_bullets", 0) <= len(ids) <= section.get("max_bullets", len(ids)):
            found.append(f"{section['id']}: {len(ids)} bullets")
        for b in section["bullets"]:
            if b.get("priority") == 1 and b["id"] not in bullet_ids:
                found.append(f"{b['id']}: priority 1 missing")
        for _, v in picked:
            if v["voice"] != selection.voice:
                found.append(f"{v['id']}: voice {v['voice']}")
            held.update({canonical(str(k), TABLE) for k in v["keywords"]})
            lines += -(-len(v["text"]) // LINE_CHARS)
        lines += ENTRY_LINES[section["kind"]] + (0 if section["kind"] in kinds else HEADING_LINES)
        kinds.add(section["kind"])
    for s in bank["sections"]:
        if s.get("priority", 1) == 1 and s["id"] not in {e["section_id"] for e in selection.layout}:
            found.append(f"{s['id']}: mandatory section missing")
    if lines > bank["meta"]["page_lines"] + 1e-9:
        found.append(f"{lines:.2f} lines over budget")
    # The cap may only be exceeded by bullets that have no way around it.
    forced = Counter()
    for s in bank["sections"]:
        if s.get("priority", 1) == 1:
            for b in s["bullets"]:
                if b.get("priority") == 1:
                    vs = [v for v in b["variants"] if v["voice"] == selection.voice]
                    forced.update(set.intersection(*({canonical(str(k), TABLE) for k in v["keywords"]}
                                                     for v in vs)))
    for k in keywords:
        if held[k.term] > max(CAP, forced[k.term]):
            found.append(f"{k.term}: held by {held[k.term]} variants")
    return found


# --- fixture: hand-checked cases ------------------------------------------------
check("selection fixture passes the bank lint", validate(FIXTURE, ALIASES)[0], [])

sel = select(FIXTURE, [], ALIASES)
check("no JD: fullest page in the first voice",
      (sel.voice, chosen(sel)),
      ("technical", ["acme_db.technical", "acme_api.technical", "acme_ci.technical",
                     "widget_core.technical", "widget_ml.technical"]))

keys = kw(Go=1.0, Kubernetes=1.0)
sel = select(FIXTURE, keys, ALIASES)
check("an optional section is swapped in when it covers the JD",
      [e["section_id"] for e in sel.layout], ["acme", "widget", "side"])
check("...and only after making room", violations(FIXTURE, sel, keys), [])
check("...covering both keywords", sorted(sel.covered), ["Go", "Kubernetes"])

sel = select(FIXTURE, kw(latency=3.0), ALIASES)
check("the voice that covers the JD wins", sel.voice, "impact")

keys = kw(Python=1.0)
sel = select(FIXTURE, keys, ALIASES)
check("anti-stuffing: no keyword in more than CAP variants", len(sel.covered["Python"]), CAP)
check("...the other slots take variants without it", "acme_api.technical" in chosen(sel), True)

sel = select(FIXTURE, keys, ALIASES, cap=0)
check("a cap below the forced count yields to it instead of failing",
      sel.covered["Python"], ["acme_db.technical"])

# Swapping side in gains the keyword but loses 0.02 of priority bonus.
sel = select(FIXTURE, kw(Kubernetes=0.01), ALIASES)
check("weak coverage doesn't pay for pushing out higher-priority bullets",
      [e["section_id"] for e in sel.layout], ["acme", "widget"])

check("exclude keeps a bullet off the page",
      any(v.startswith("widget_ml") for v in chosen(select(FIXTURE, [], ALIASES, exclude={"widget_ml"}))),
      False)

check("weakest_filler picks a priority-3 bullet",
      weakest_filler(select(FIXTURE, [], ALIASES), FIXTURE, [], ALIASES), "widget_ml")
keys = kw(Go=1.0, Kubernetes=1.0)
check("weakest_filler treats a priority-1 bullet in a priority-3 section as filler",
      weakest_filler(select(FIXTURE, keys, ALIASES), FIXTURE, keys, ALIASES), "side_go")
sel = select(FIXTURE, [], ALIASES, exclude={"widget_ml"})
check("weakest_filler returns None when no priority-3 bullet is selected",
      weakest_filler(sel, FIXTURE, [], ALIASES), None)

# --- two-page fallback (render stubbed: no pdflatex needed) ---------------------------
import tailor.__main__ as cli  # noqa: E402
from tailor.render import PageOverflow  # noqa: E402

rendered = []


def fake_render(bank, layout, out_dir, voice):
    rendered.append([b["variant_id"] for e in layout for b in e["bullets"]])
    if len(rendered) == 1:
        raise PageOverflow("resume is 2 pages")
    return out_dir / "resume.pdf"


cli.render = fake_render
sel, _ = cli.tailor(FIXTURE, ALIASES, [], Path("unused"))
check("on two pages, the weakest priority-3 bullet is dropped and the page re-solved",
      ("widget_ml.technical" in rendered[0], "widget_ml.technical" in chosen(sel), len(rendered)),
      (True, False, 2))


def always_overflow(bank, layout, out_dir, voice):
    raise PageOverflow("resume is 2 pages")


cli.render = always_overflow
check("still two pages after the drop is an error",
      _raises(PageOverflow, lambda: cli.tailor(FIXTURE, ALIASES, [], Path("unused"))), True)

# --- fixture: every real JD -------------------------------------------------------
vocab = vocabulary(FIXTURE, ALIASES, TAXONOMY) | set(EXPECTED["vocabulary"])
for name, spec in EXPECTED["jds"].items():
    jd = (ROOT / "tests" / "jds" / f"{name}.txt").read_text(encoding="utf-8")
    keys = extract(jd, vocab, ALIASES, TAXONOMY, spec["company"])
    start = time.perf_counter()
    first = select(FIXTURE, keys, ALIASES)
    elapsed = time.perf_counter() - start
    check(f"{name}: constraints hold", violations(FIXTURE, first, keys), [])
    check(f"{name}: deterministic", select(FIXTURE, keys, ALIASES), first)
    check(f"{name}: under 200 ms", elapsed < 0.2, True)

# --- real bank ------------------------------------------------------------------------
if BANK_PATH.exists() and shutil.which("pdflatex"):
    bank = load_yaml(BANK_PATH)
    check("real bank, no JD: the SWE base resume", select(bank, [], ALIASES).layout, full_layout(bank))
    vocab = vocabulary(bank, ALIASES, TAXONOMY)
    with tempfile.TemporaryDirectory() as tmp:
        for name, spec in EXPECTED["jds"].items():
            jd = (ROOT / "tests" / "jds" / f"{name}.txt").read_text(encoding="utf-8")
            keys = extract(jd, vocab, ALIASES, TAXONOMY, spec["company"])
            start = time.perf_counter()
            sel = select(bank, keys, ALIASES)
            elapsed = time.perf_counter() - start
            check(f"real bank, {name}: constraints hold", violations(bank, sel, keys), [])
            check(f"real bank, {name}: under 200 ms", elapsed < 0.2, True)
            # render() raises PageOverflow on a second page.
            pdf = render(bank, sel.layout, Path(tmp) / name, sel.voice)
            check(f"real bank, {name}: first try renders to one page", pdf.exists(), True)
else:
    print("SKIP  real-bank checks need resume/bank.yaml and pdflatex")

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
