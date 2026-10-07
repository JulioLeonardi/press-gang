"""Load the variant bank and the alias table."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BANK_PATH = ROOT / "resume" / "bank.yaml"
ALIASES_PATH = ROOT / "resume" / "aliases.yaml"


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def alias_table(aliases: dict) -> dict[str, str]:
    """Lowercased spelling -> canonical term. A canonical term maps to itself."""
    table = {}
    for canonical, others in aliases.items():
        for term in [canonical, *(others or [])]:
            table[str(term).lower()] = canonical
    return table


def canonical(term: str, table: dict[str, str]) -> str:
    return table.get(term.lower(), term)


def spellings(term: str, aliases: dict, table: dict[str, str]) -> list[str]:
    """Every way `term` may be written: its canonical form plus all aliases."""
    canon = canonical(term, table)
    return [canon, *(aliases.get(canon) or [])]
