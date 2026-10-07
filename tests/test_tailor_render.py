"""Render: verbatim assertion and the one-page check.

Run: python tests/test_tailor_render.py
The PDF checks need resume/bank.yaml (gitignored) and pdflatex; they skip
when either is missing.
"""

import copy
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tailor.bank import BANK_PATH, load_yaml
from tailor.render import (
    PageOverflow,
    VerbatimError,
    assert_verbatim,
    full_layout,
    render,
    tex,
)

FIXTURE = load_yaml(ROOT / "tests" / "fixtures" / "bank" / "valid.yaml")
failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}, got {actual!r}")
        failures.append(label)


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


# --- verbatim assertion -------------------------------------------------------
layout = full_layout({"sections": [dict(FIXTURE["sections"][0], priority=1)]})
check("bank text passes the verbatim assertion",
      raises(VerbatimError, lambda: assert_verbatim(layout, FIXTURE)), False)

mutated = copy.deepcopy(layout)
mutated[0]["bullets"][0]["text"] += " "
check("a trailing space trips the verbatim assertion",
      raises(VerbatimError, lambda: assert_verbatim(mutated, FIXTURE)), True)

mutated = copy.deepcopy(layout)
mutated[0]["bullets"][0]["text"] = mutated[0]["bullets"][0]["text"].replace("106", "110")
check("a changed number trips the verbatim assertion",
      raises(VerbatimError, lambda: assert_verbatim(mutated, FIXTURE)), True)

mutated = copy.deepcopy(layout)
mutated[0]["bullets"][0]["variant_id"] = "acme_rls.impact"
check("another variant's id with this text trips the verbatim assertion",
      raises(VerbatimError, lambda: assert_verbatim(mutated, FIXTURE)), True)

# --- escaping -----------------------------------------------------------------
check("LaTeX specials are escaped", tex("~400% & R&D #1"), r"\raisebox{0.5ex}{\texttildelow}400\% \& R\&D \#1")
check("en dash becomes --", tex("May 2026 – July 2026"), "May 2026 -- July 2026")

# --- PDF ----------------------------------------------------------------------
if BANK_PATH.exists() and shutil.which("pdflatex"):
    bank = load_yaml(BANK_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        pdf = render(bank, full_layout(bank), Path(tmp), "technical")
        check("base resume renders to one page", pdf.name, "Julio_Leonardi_Resume.pdf")
        check("selection.json is written", (Path(tmp) / "selection.json").exists(), True)

        # Every section, priority 3 included, can't fit on one page.
        everything = copy.deepcopy(bank)
        for section in everything["sections"]:
            section["priority"] = 1
        check("an over-long layout raises PageOverflow",
              raises(PageOverflow, lambda: render(everything, full_layout(everything),
                                                  Path(tmp) / "long", "technical")), True)
else:
    print("SKIP  PDF checks need resume/bank.yaml and pdflatex")

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
