"""Bank lint: each broken fixture trips its own rule and nothing else.

Run: python tests/test_tailor_validate.py
"""

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tailor.bank import ALIASES_PATH, BANK_PATH, load_yaml
from tailor.validate import validate

ALIASES = load_yaml(ALIASES_PATH)
VALID = load_yaml(ROOT / "tests" / "fixtures" / "bank" / "valid.yaml")
failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}, got {actual!r}")
        failures.append(label)


def broken(mutate):
    """Lint a copy of the valid fixture after `mutate(bank, rls, clf)`."""
    bank = copy.deepcopy(VALID)
    rls, clf = bank["sections"][0]["bullets"]
    mutate(bank, rls, clf)
    return validate(bank, ALIASES)


def trips(errors, needle):
    return len(errors) == 1 and needle in errors[0]


errors, warnings = validate(VALID, ALIASES)
check("valid fixture has no errors", errors, [])
check("category keyword not in text only warns",
      warnings, ["acme_rls.technical: category keyword 'security' not in text",
                 "acme_rls.impact: category keyword 'security' not in text"])

# --- numbers ------------------------------------------------------------------
def rounded(bank, rls, clf):
    rls["variants"][1]["text"] = "Locked down tenant data with 100+ database policies, rolled out over 14 migrations"
errors, _ = broken(rounded)
check("'100+' where the fact says 106 is an error", trips(errors, "['100+']"), True)

def dropped(bank, rls, clf):
    rls["variants"][1]["text"] = "Locked down tenant data with database policies"
check("a variant may omit a locked number", broken(dropped)[0], [])

def fact_drift(bank, rls, clf):
    rls["fact"] = "Wrote 106 Postgres RLS policies."
check("a locked number missing from the fact is an error",
      trips(broken(fact_drift)[0], "not in its fact"), True)

check("names like T4 and YOLOv8 are not numeric claims",
      broken(lambda b, r, c: c["variants"][0].update(
          text="Trained a classifier on T4 GPUs with YOLOv8 at 82% holdout accuracy"))[0], [])

# --- voices -------------------------------------------------------------------
# Also trips the partial-coverage rule, since 'impact' now covers one bullet.
check("missing voice is an error",
      any("missing voice" in e for e in broken(lambda b, r, c: r["variants"][1].pop("voice"))[0]), True)

def unknown_voice(bank, rls, clf):
    rls["variants"][1]["voice"] = "casual"
    clf["variants"][1]["voice"] = "casual"
check("voice outside meta.voices is an error",
      sum("not in meta.voices" in e for e in broken(unknown_voice)[0]), 2)

check("a voice covering only some bullets is an error",
      trips(broken(lambda b, r, c: c["variants"].pop(1))[0], "covers 1/2 bullets"), True)

# --- keywords -----------------------------------------------------------------
check("overclaiming keyword is an error",
      trips(broken(lambda b, r, c: r["variants"][0]["keywords"].append("Kubernetes"))[0],
            "'Kubernetes' not supported"), True)
# The valid fixture already relies on aliases (RLS -> Row-Level Security), close
# forms (classifier -> classification) and the stack line (Docker).
check("without the stack line, 'Docker' is unsupported",
      trips(broken(lambda b, r, c: b["sections"][0].pop("stack"))[0], "'Docker' not supported"), True)

# --- structure ----------------------------------------------------------------
check("text over max_chars is an error",
      trips(broken(lambda b, r, c: r["variants"][1].update(text="Locked down data " * 15))[0],
            "over max_chars"), True)
check("duplicate variant id is an error",
      trips(broken(lambda b, r, c: c["variants"][0].update(id="acme_rls.technical"))[0],
            "duplicate id"), True)
check("min_bullets above the bullet count is an error",
      trips(broken(lambda b, r, c: b["sections"][0].update(min_bullets=3))[0], "can't be met"), True)

# --- the real bank (local only; gitignored, so absent in CI) --------------------
if BANK_PATH.exists():
    check("resume/bank.yaml has no errors", validate(load_yaml(BANK_PATH), ALIASES)[0], [])
else:
    print("SKIP  resume/bank.yaml not present")

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
