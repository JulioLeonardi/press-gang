"""JD text -> weighted keyword set.

Exact pass over the bank's vocabulary (alias-aware, section-weighted), then
taxonomy expansion. The optional LLM pass is not built yet.

Run: python -m tailor.extract path/to/jd.txt [company]
"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from tailor.bank import ALIASES_PATH, BANK_PATH, ROOT, alias_table, canonical, load_yaml

TAXONOMY_PATH = ROOT / "resume" / "taxonomy.yaml"
DEFAULT_FACTOR = 0.5
UNKNOWN_WEIGHT = 0.7
NICE_WEIGHT = 0.5

# Header text -> section weight; first match wins, so "Preferred
# qualifications" is nice-to-have before "qualification" can make it required,
# and "Physical Requirements" is ignored before "requirement" can.
_SECTIONS = [
    (0.5, r"nice.to.have|bonus|preferred|desirable|good to have|\bplus\b|extra credit"),
    (0.0, r"^about (us|the company|[\w&.-]+)$|who we are|benefit|what we offer|why (join|you should"
          r"|work)|perks|what's it like|life at|equal (employment )?opportunit|\beeo\b|diversity"
          r"|privacy|interview|hiring process|job details|we welcome|compensation|salary|your first"
          r"|join us|application tip|our values|physical|work environment|wir bieten"),
    (1.0, r"requirement|qualification|what you('ll| will)? need|who you are|you have|great for this"
          r"|ideal candidate|profile|skills|looking for|must.have|about you|you know|das bringst du"),
    (0.8, r"responsibilit|what you('ll| will) do|you will|day to day|your role|the role|duties"
          r"|mission|the job|aufgaben|impact|you'll be doing"),
]
_SECTIONS = [(w, re.compile(p)) for w, p in _SECTIONS]
_BLOCK_TAG = re.compile(r"<(?:/?(?:p|div|ul|ol|h[1-6]|tr|section)|br)\b[^>]*>", re.IGNORECASE)
_BOLD_LINE = re.compile(r"\s*<(strong|b)\b[^>]*>((?:(?!</?\1\b).)*)</\1>\s*", re.IGNORECASE)
_HEADING = "\x02"
# "Fintech experience would be a plus" inside a requirements list is still a
# nice-to-have. Checked per clause, so one "a plus" doesn't discount a whole
# bullet; "e.g. Kotlin" is not a clause break.
_CLAUSE_BREAK = re.compile(r"(?<=;)\s+|(?<!e\.g\.)(?<!i\.e\.)(?<=\.)\s+(?=[A-Z])|\s[-–—]\s")
_INLINE_NICE = re.compile(r"\ba plus\b|\bbonus\b|nice.to.have|\bpreferred\b|\bdesirable\b"
                          r"|\badvantage\b|\b(is|are) valued\b", re.IGNORECASE)


@dataclass
class Keyword:
    term: str            # canonical form
    weight: float
    source: Literal["exact", "inferred", "llm"]
    evidence: str        # JD snippet or "cloud -> Kubernetes"


def _lines(jd: str) -> list[tuple[str, bool]]:
    """Plain-text lines, each paired with whether the HTML marked it as a heading."""
    # Greenhouse serves entity-escaped HTML; plain pasted text passes through.
    for _ in range(2):
        if "&lt;" in jd:
            jd = html.unescape(jd)
    jd = re.sub(r"<li\b[^>]*>", "\n- ", jd, flags=re.IGNORECASE)
    jd = re.sub(r"<h[1-6]\b[^>]*>", "\n" + _HEADING, jd, flags=re.IGNORECASE)
    jd = _BLOCK_TAG.sub("\n", jd)
    lines = []
    for raw in jd.splitlines():
        marked = raw.lstrip().startswith(_HEADING) or bool(_BOLD_LINE.fullmatch(raw))
        text = html.unescape(re.sub(r"<[^>]+>", "", raw)).replace(_HEADING, "")
        text = re.sub(r"[ \t\xa0]+", " ", text).strip()
        if text:
            lines.append((text, marked))
    return lines


def _section_weight(line: str, marked: bool) -> float | None:
    """Weight a header line opens, or None if the line isn't a header."""
    if line.startswith("- "):
        return None
    words = line.split()
    # Unmarked (plain-text) headers must be short and not read as a sentence.
    if not marked and (len(words) > 8 or line.endswith(".")):
        return None
    if len(line) > 80:
        return None
    key = re.sub(r"[\s:!?.…]+$", "", line.lower().replace("’", "'"))
    for weight, pattern in _SECTIONS:
        if pattern.search(key):
            return weight
    return UNKNOWN_WEIGHT if marked else None


def _pattern(spelling: str) -> re.Pattern:
    # All-caps and short spellings are case-sensitive: "Go" not "go", "REST" not "rest".
    flags = 0 if spelling.isupper() or len(spelling) <= 3 else re.IGNORECASE
    return re.compile(rf"(?<![\w+#]){re.escape(spelling)}(?![\w+#])", flags)


def vocabulary(bank: dict, aliases: dict, taxonomy: dict) -> set[str]:
    """Every canonical term worth searching for."""
    table = alias_table(aliases)
    terms = set(aliases)
    for term, entry in taxonomy.items():
        terms.add(term)
        terms.update((entry or {}).get("implies") or [])
    for section in bank.get("sections") or []:
        for bullet in section.get("bullets") or []:
            for variant in bullet.get("variants") or []:
                terms.update(variant.get("keywords") or [])
    return {canonical(str(t), table) for t in terms}


def _bonus(weights: list[float]) -> float:
    """1st hit counts fully, 2nd adds 0.3 of its weight, later hits 0.1 each."""
    weights = sorted((w for w in weights if w > 0), reverse=True)
    if not weights:
        return 0.0
    return weights[0] + 0.3 * sum(weights[1:2]) + 0.1 * sum(weights[2:])


def extract(jd: str, vocab: Iterable[str], aliases: dict, taxonomy: dict,
            company: str | None = None) -> list[Keyword]:
    """`company` is the employer's name; "At Stripe we..." is not a Stripe skill."""
    table = alias_table(aliases)
    clauses = []
    weight = UNKNOWN_WEIGHT
    for text, marked in _lines(jd):
        opened = _section_weight(text, marked)
        if opened is not None:
            weight = opened
        for clause in _CLAUSE_BREAK.split(text):
            clauses.append((clause, min(weight, NICE_WEIGHT) if _INLINE_NICE.search(clause) else weight))

    found: dict[str, Keyword] = {}
    for term in sorted(vocab):
        if company and term.lower() == company.strip().lower():
            continue
        hits = {}  # (clause index, start) -> (weight, snippet); dedupes overlapping spellings
        for spelling in {term, *(aliases.get(term) or [])}:
            pattern = _pattern(str(spelling))
            for i, (text, w) in enumerate(clauses):
                for m in pattern.finditer(text):
                    hits.setdefault((i, m.start()), (w, text[max(0, m.start() - 40): m.end() + 40]))
        score = _bonus([w for w, _ in hits.values()])
        if score > 0:
            evidence = max(hits.values(), key=lambda h: h[0])[1].strip()
            found[term] = Keyword(term, round(score, 3), "exact", evidence)

    inferred: dict[str, Keyword] = {}
    for parent, entry in taxonomy.items():
        parent = canonical(parent, table)
        if parent not in found:
            continue
        entry = entry or {}
        factor = entry.get("factor", DEFAULT_FACTOR)
        for child in entry.get("implies") or []:
            child = canonical(str(child), table)
            score = round(found[parent].weight * factor, 3)
            if child in found or score <= inferred.get(child, Keyword("", 0, "inferred", "")).weight:
                continue
            inferred[child] = Keyword(child, score, "inferred", f"{parent} -> {child}")

    return sorted([*found.values(), *inferred.values()], key=lambda k: (-k.weight, k.term))


def main(argv: list[str]) -> int:
    aliases, taxonomy = load_yaml(ALIASES_PATH), load_yaml(TAXONOMY_PATH)
    bank = load_yaml(BANK_PATH) if BANK_PATH.exists() else {}
    jd = Path(argv[0]).read_text(encoding="utf-8")
    company = argv[1] if len(argv) > 1 else None
    for k in extract(jd, vocabulary(bank, aliases, taxonomy), aliases, taxonomy, company):
        print(f"{k.weight:6.3f}  {k.source:8}  {k.term:28}  {k.evidence[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
