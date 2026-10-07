"""Bullet line measurement: wrap counts from pdflatex, the cache, the fallback.

Run: python tests/test_tailor_measure.py
Needs pdflatex; skips without it.
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tailor.measure as measure  # noqa: E402
from tailor.select import WRAP_LINES, bullet_lines  # noqa: E402

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}, got {actual!r}")
        failures.append(label)


SHORT = "Built a small service in Python"
LONG = ("Built a small service in Python that reads events from a queue, validates them against a schema, "
        "stores the results in a relational database and serves them to three internal dashboards every hour")


def bank(*texts):
    return {"sections": [{"bullets": [{"variants": [{"text": t} for t in texts]}]}]}


check("bullet_lines: a measured 2-line bullet costs one wrap more",
      bullet_lines(SHORT, {SHORT: 2}), 1 + WRAP_LINES)
check("bullet_lines: unmeasured text falls back to the character guess", bullet_lines(SHORT, None), 1)

if shutil.which("pdflatex"):
    with tempfile.TemporaryDirectory() as tmp:
        measure.CACHE_PATH = Path(tmp) / "line_cache.json"
        counts = measure.line_counts(bank(SHORT, LONG, "100% & ~30 #1 R&D_x"))
        check("a short bullet is one line", counts[SHORT], 1)
        check("a long bullet is two lines", counts[LONG], 2)
        check("LaTeX specials measure without breaking the run", counts["100% & ~30 #1 R&D_x"], 1)

        def no_pdflatex(template, texts):
            raise AssertionError(f"pdflatex ran for cached texts: {texts}")

        typeset, measure._typeset = measure._typeset, no_pdflatex
        check("cached texts don't rerun pdflatex", measure.line_counts(bank(SHORT, LONG)),
              {SHORT: 1, LONG: 2})
        measure._typeset = typeset
else:
    print("SKIP  measurement checks need pdflatex")

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
