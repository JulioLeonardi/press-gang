"""Pick sections, bullets and one variant per bullet to cover a JD's keywords.

Exact branch-and-bound per voice; the best voice wins. Deterministic: options
are tried in bank order and only a strictly better score replaces the
incumbent, so ties go to the first layout found, and a bullet's first-listed
variant is its default wording.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations, product

from tailor.bank import alias_table, canonical
from tailor.extract import Keyword

CAP = 2
# g(n): credit for a keyword held by n selected variants; the 3rd+ earns nothing.
_G = [0.0, 1.0, 1.3]
# Effective priority (the worse of bullet and section) -> bonus per bullet, so
# a fuller, higher-priority page wins when coverage ties.
PRIORITY_BONUS = {1: 0.03, 2: 0.02, 3: 0.01}
# Vertical cost in bullet lines (14pt each), measured with \pagetotal on
# resume/template.tex. meta.page_lines is the budget for everything below
# Technical Skills, minus Awards.
ENTRY_LINES = {"experience": 2.31, "involvement": 2.31, "project": 1.44}
HEADING_LINES = 1.71        # "Experience", "Projects", ...
WRAP_LINES = 12 / 14        # each extra line of a wrapped bullet
LINE_CHARS = 115            # wrap guess when tailor.measure has no count
_EPS = 1e-9


class NoLayout(RuntimeError):
    """No voice can fill the mandatory sections within the constraints."""


@dataclass
class Selection:
    voice: str
    layout: list[dict]                   # render.py's format
    score: float
    covered: dict[str, list[str]]        # term -> variant ids holding it
    uncovered: list[Keyword] = field(default_factory=list)


def _g(n: int) -> float:
    return _G[min(n, len(_G) - 1)]


def bullet_lines(text: str, lines: dict[str, int] | None) -> float:
    """Vertical cost of one bullet; `lines` is tailor.measure.line_counts()."""
    wrapped = (lines or {}).get(text) or -(-len(text) // LINE_CHARS)
    return 1 + (wrapped - 1) * WRAP_LINES


def _variant_terms(variant: dict, table: dict[str, str], weights: dict[str, float]) -> frozenset:
    return frozenset(t for t in (canonical(str(k), table) for k in variant.get("keywords") or [])
                     if t in weights)


def _options(section: dict, voice: str, table, weights, exclude, lines) -> list[dict] | None:
    """Every legal bullet set with one variant each, in tie-break order.

    None if the voice has no variants here or an excluded bullet is required.
    """
    sp = section.get("priority", 1)
    bullets = [b for b in section["bullets"]
               if any(v["voice"] == voice for v in b["variants"])]
    if len(bullets) < len(section["bullets"]):
        return None
    required = [b for b in bullets if b.get("priority") == 1]
    if any(b["id"] in exclude for b in required):
        return None
    optional = [b for b in bullets if b.get("priority") != 1 and b["id"] not in exclude]
    lo = section.get("min_bullets", 0)
    hi = section.get("max_bullets", len(bullets))
    order = {b["id"]: i for i, b in enumerate(bullets)}
    # Variants with the same JD keywords and height can only tie, and ties go
    # to the first listed, so the rest never need trying.
    distinct = {}
    for b in bullets:
        seen, distinct[b["id"]] = set(), []
        for v in b["variants"]:
            key = (_variant_terms(v, table, weights), bullet_lines(v["text"], lines))
            if v["voice"] == voice and key not in seen:
                seen.add(key)
                distinct[b["id"]].append(v)

    options = []
    for size in range(hi - len(required), -1, -1):     # fuller first
        if len(required) + size < max(lo, 1):
            break
        for extra in combinations(optional, size):
            chosen = sorted([*required, *extra], key=lambda b: order[b["id"]])
            for variants in product(*(distinct[b["id"]] for b in chosen)):
                terms = Counter(t for v in variants for t in _variant_terms(v, table, weights))
                options.append({
                    "picks": list(zip(chosen, variants)),
                    "terms": terms,
                    "lines": ENTRY_LINES[section["kind"]] + sum(bullet_lines(v["text"], lines) for v in variants),
                    "bonus": sum(PRIORITY_BONUS[max(b.get("priority", 3), sp)] for b in chosen),
                })
    return options


def _gain(terms: Counter, counts: Counter, weights: dict[str, float]) -> float:
    return sum(weights[t] * (_g(counts[t] + n) - _g(counts[t])) for t, n in terms.items())


def _solve_voice(bank: dict, voice: str, weights, table, cap: int, exclude, lines) -> tuple | None:
    budget = bank["meta"]["page_lines"]
    sections, choices = [], []
    for section in bank["sections"]:
        opts = _options(section, voice, table, weights, exclude, lines)
        mandatory = section.get("priority", 1) == 1
        if mandatory and not opts:
            return None
        sections.append(section)
        choices.append((opts or []) + ([] if mandatory else [None]))

    # A keyword every variant of a mandatory bullet holds can't be avoided, so
    # its limit rises to that count rather than making the bank unsolvable.
    forced = Counter()
    for section, opts in zip(sections, choices):
        if None in opts:
            continue
        for bullet, _ in opts[0]["picks"]:
            if bullet.get("priority") == 1:
                held = [_variant_terms(v, table, weights) for v in bullet["variants"] if v["voice"] == voice]
                forced.update(frozenset.intersection(*held))
    limit = {t: max(cap, forced[t]) for t in weights}

    n = len(sections)
    # Upper bound on what sections i.. can still add: each one's best gain from
    # an empty page (coverage is submodular, so it never grows later).
    best_alone = [max(((_gain(o["terms"], Counter(), weights) + o["bonus"]) if o else 0.0)
                      for o in opts) for opts in choices]
    min_lines = [min((o["lines"] if o else 0.0) for o in opts) for opts in choices]
    bound, floor = [0.0] * (n + 1), [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        bound[i] = bound[i + 1] + best_alone[i]
        floor[i] = floor[i + 1] + min_lines[i]

    best = {"score": float("-inf"), "picks": None}
    counts: Counter = Counter()
    picked: list = [None] * n

    def walk(i: int, lines: float, kinds: frozenset, score: float) -> None:
        if lines + floor[i] > budget + _EPS or score + bound[i] <= best["score"] + _EPS:
            return
        if i == n:
            best["score"], best["picks"] = score, list(picked)
            return
        kind = sections[i]["kind"]
        for opt in choices[i]:
            if opt is None:
                picked[i] = None
                walk(i + 1, lines, kinds, score)
                continue
            if any(counts[t] + c > limit[t] for t, c in opt["terms"].items()):
                continue
            extra = opt["lines"] + (0.0 if kind in kinds else HEADING_LINES)
            gained = _gain(opt["terms"], counts, weights) + opt["bonus"]
            counts.update(opt["terms"])
            picked[i] = opt
            walk(i + 1, lines + extra, kinds | {kind}, score + gained)
            counts.subtract(opt["terms"])

    walk(0, 0.0, frozenset(), 0.0)
    if best["picks"] is None:
        return None
    return best["score"], [(s, o) for s, o in zip(sections, best["picks"]) if o]


def select(bank: dict, keywords: list[Keyword], aliases: dict, cap: int = CAP,
           exclude: frozenset = frozenset(), lines: dict[str, int] | None = None) -> Selection:
    """Best layout over all voices. `exclude` holds bullet ids that must not appear;
    `lines` maps bullet text to its measured line count (tailor.measure)."""
    table = alias_table(aliases)
    weights = {k.term: k.weight for k in keywords}
    winner = None
    for voice in bank["meta"]["voices"]:
        solved = _solve_voice(bank, voice, weights, table, cap, exclude, lines)
        if solved and (winner is None or solved[0] > winner[1][0] + _EPS):
            winner = (voice, solved)
    if winner is None:
        raise NoLayout("no voice can fill the mandatory sections on one page")

    voice, (score, chosen) = winner
    layout, covered = [], {}
    for section, opt in chosen:
        layout.append({"section_id": section["id"],
                       "bullets": [{"variant_id": v["id"], "text": v["text"]} for _, v in opt["picks"]]})
        for _, v in opt["picks"]:
            for term in sorted(_variant_terms(v, table, weights)):
                covered.setdefault(term, []).append(v["id"])
    return Selection(voice, layout, round(score, 4), covered,
                     [k for k in keywords if k.term not in covered])


def weakest_filler(selection: Selection, bank: dict, keywords: list[Keyword], aliases: dict) -> str | None:
    """Id of the selected priority-3 bullet whose removal costs the least coverage."""
    table = alias_table(aliases)
    weights = {k.term: k.weight for k in keywords}
    sections = {s["id"]: s for s in bank["sections"]}
    held = {t: len(ids) for t, ids in selection.covered.items()}
    candidates = []
    for entry in selection.layout:
        section = sections[entry["section_id"]]
        for bullet in section["bullets"]:
            variant = next((v for v in bullet["variants"]
                            if v["id"] in {b["variant_id"] for b in entry["bullets"]}), None)
            if variant is None or max(bullet.get("priority", 3), section.get("priority", 1)) != 3:
                continue
            terms = _variant_terms(variant, table, weights)
            loss = sum(weights[t] * (_g(held[t]) - _g(held[t] - 1)) for t in terms)
            candidates.append((loss, variant["id"], bullet["id"]))
    return min(candidates)[2] if candidates else None


# Match percentage, for deciding whether to apply. Only keywords the JD names
# count: an inferred one ("cloud -> Terraform") is a tool the JD never asked
# for. "Core" is what sits under the requirements header (weight 1.0) or
# repeats often enough to reach 0.9.
CORE_WEIGHT = 0.9
CORE_SHARE = 0.6
# (floor, label), checked in order. A judgment call from 8 JDs, not calibrated.
MATCH_BANDS = [(75, "strong"), (55, "fair"), (0, "weak")]


def match(selection: Selection, keywords: list[Keyword]) -> dict | None:
    """{percent, band, core, named}: weighted coverage of the JD's named keywords,
    with core requirements counting 60%. None when the JD names nothing in the bank."""
    def coverage(ks):
        total = sum(k.weight for k in ks)
        return 100 * sum(k.weight for k in ks if k.term in selection.covered) / total if total else None

    named = [k for k in keywords if k.source == "exact"]
    named_pct = coverage(named)
    if named_pct is None:
        return None
    core_pct = coverage([k for k in named if k.weight >= CORE_WEIGHT])
    percent = round(named_pct if core_pct is None else CORE_SHARE * core_pct + (1 - CORE_SHARE) * named_pct)
    band = next(label for floor, label in MATCH_BANDS if percent >= floor)
    return {"percent": percent, "band": band, "named": round(named_pct),
            "core": None if core_pct is None else round(core_pct)}
