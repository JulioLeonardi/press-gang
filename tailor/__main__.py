"""JD -> tailored one-page PDF plus an explanation of the selection.

Run: python -m tailor path/to/jd.txt [company] [posting_id]
The PDF lands in out/{posting_id}/ (default: the JD file's name).
"""

from __future__ import annotations

import sys
from pathlib import Path

from tailor.bank import ALIASES_PATH, BANK_PATH, load_yaml
from tailor.extract import TAXONOMY_PATH, extract, vocabulary
from tailor.measure import line_counts
from tailor.render import OUT_DIR, PageOverflow, render
from tailor.select import Selection, match, select, weakest_filler
from tailor.validate import validate


def tailor(bank: dict, aliases: dict, keywords: list, out_dir: Path) -> tuple[Selection, Path]:
    """Select and render; on two pages, drop the weakest priority-3 bullet once and retry."""
    lines = line_counts(bank)
    selection = select(bank, keywords, aliases, lines=lines)
    try:
        return selection, render(bank, selection.layout, out_dir, selection.voice)
    except PageOverflow:
        dropped = weakest_filler(selection, bank, keywords, aliases)
        if dropped is None:
            raise
        selection = select(bank, keywords, aliases, exclude=frozenset({dropped}), lines=lines)
        return selection, render(bank, selection.layout, out_dir, selection.voice)


def explain(selection: Selection, keywords: list) -> list[str]:
    weights = {k.term: k.weight for k in keywords}
    m = match(selection, keywords)
    lines = [f"match {m['percent']}% ({m['band']}): core {m['core']}%, all named {m['named']}%"
             if m else "match: the JD names nothing in the bank",
             f"score {selection.score}  voice {selection.voice}", "", "covered:"]
    for term in sorted(selection.covered, key=lambda t: (-weights[t], t)):
        lines.append(f"  {weights[term]:6.3f}  {term:28}  {', '.join(selection.covered[term])}")
    lines += ["", "uncovered:"]
    for k in selection.uncovered:
        lines.append(f"  {k.weight:6.3f}  {k.term:28}  {k.source:8}  {k.evidence[:60]}")
    return lines


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip())
        return 2
    bank, aliases, taxonomy = load_yaml(BANK_PATH), load_yaml(ALIASES_PATH), load_yaml(TAXONOMY_PATH)
    errors, _ = validate(bank, aliases)
    if errors:
        print("bank has errors; run python -m tailor.validate")
        return 1
    jd_path = Path(argv[0])
    company = argv[1] if len(argv) > 1 else None
    posting_id = argv[2] if len(argv) > 2 else jd_path.stem
    keywords = extract(jd_path.read_text(encoding="utf-8"), vocabulary(bank, aliases, taxonomy),
                       aliases, taxonomy, company)
    selection, pdf = tailor(bank, aliases, keywords, OUT_DIR / posting_id)
    print("\n".join(explain(selection, keywords)))
    print(f"\n{pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
