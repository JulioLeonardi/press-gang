"""Keyword extraction against real JDs (tests/jds) plus targeted edge cases.

Run: python tests/test_tailor_extract.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tailor.bank import ALIASES_PATH, load_yaml
from tailor.extract import TAXONOMY_PATH, extract, vocabulary

ALIASES = load_yaml(ALIASES_PATH)
TAXONOMY = load_yaml(TAXONOMY_PATH)
EXPECTED = load_yaml(ROOT / "tests" / "jds" / "expected.yaml")
VOCAB = vocabulary({}, ALIASES, TAXONOMY) | set(EXPECTED["vocabulary"])
failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}, got {actual!r}")
        failures.append(label)


def run(jd, company=None):
    return {k.term: k for k in extract(jd, VOCAB, ALIASES, TAXONOMY, company)}


# --- real JDs -----------------------------------------------------------------
for name, spec in EXPECTED["jds"].items():
    found = run((ROOT / "tests" / "jds" / f"{name}.txt").read_text(encoding="utf-8"), spec["company"])
    exact = {t for t, k in found.items() if k.source == "exact"}
    inferred = {t for t, k in found.items() if k.source == "inferred"}
    check(f"{name}: exact terms found", sorted(set(spec["exact"]) - exact), [])
    check(f"{name}: inferred terms found", sorted(set(spec.get("inferred", [])) - inferred), [])
    check(f"{name}: absent terms absent", sorted(set(spec.get("absent", [])) & set(found)), [])
    for term, weight in (spec.get("weights") or {}).items():
        check(f"{name}: {term} weight", found[term].weight if term in found else None, weight)

# --- sections in plain (pasted) text -------------------------------------------
plain = """Backend Engineer (m/w/d)
Requirements:
- Python and PostgreSQL
Nice to have:
- Kotlin
Benefits
- Free Docker stickers
"""
found = run(plain)
check("plain text: Requirements header weighs 1.0", found["Python"].weight, 1.0)
check("plain text: Nice to have header weighs 0.5", found["Kotlin"].weight, 0.5)
check("plain text: Benefits section is ignored", "Docker" in found, False)

# --- matching edge cases -------------------------------------------------------
found = run("We use NoSQL stores, and the rest is Go-free. Let's go!")
check("'NoSQL' does not match SQL", "SQL" in found, False)
check("lowercase 'rest' does not match REST", "REST" in found, False)
check("lowercase 'go' does not match Go", found["Go"].evidence.count("Go-free"), 1)

found = run("Experience with k8s and Postgres; Golang is a plus")
check("alias k8s -> Kubernetes", found["Kubernetes"].source, "exact")
check("alias Postgres -> PostgreSQL", found["PostgreSQL"].source, "exact")
check("inline 'a plus' halves only its own clause", (found["PostgreSQL"].weight, found["Go"].weight), (0.7, 0.5))

found = run("Python, Python, Python, Python")
check("repeat hits: 1.0 + 0.3 + 0.1 + 0.1 (x0.7 unknown section)", found["Python"].weight, round(0.7 * 1.5, 3))

found = run("Cloud experience required", company=None)
check("cloud expands to Kubernetes at half weight", (found["Kubernetes"].source, found["Kubernetes"].weight),
      ("inferred", 0.35))
check("inferred evidence names the parent", found["Kubernetes"].evidence, "cloud -> Kubernetes")

found = run("Payments and Stripe experience")
check("an exact hit is not overwritten by inference", found["Stripe"].source, "exact")

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
