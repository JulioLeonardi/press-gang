"""Bank lint: fail loudly on anything that could misstate a fact or break selection.

Run: python -m tailor.validate [path/to/bank.yaml]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tailor.bank import (
    ALIASES_PATH,
    BANK_PATH,
    alias_table,
    canonical,
    load_yaml,
    spellings,
)

# "106", "82%", "100k+", "1:1", "~30" (as "30"). The lookbehind skips digits
# glued to letters, so "T4" and "YOLOv8" are names, not claims.
NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,:]\d+)*(?:k\+|k|\+|%)?", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9+#]+")
# Longest first; a stem must keep at least 4 letters.
_SUFFIXES = ("ication", "ability", "ation", "ility", "ment", "able", "ing",
             "ion", "ies", "ed", "er", "es", "s", "y")


def numbers(text: str) -> set[str]:
    return {n.lower() for n in NUMBER.findall(text)}


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def _close(a: str, b: str) -> bool:
    """Same word up to inflection: 'classifier' ~ 'classification'."""
    a, b = _stem(a), _stem(b)
    if min(len(a), len(b)) < 4:
        return a == b
    return a.startswith(b) or b.startswith(a)


def supported(keyword: str, text: str, aliases: dict, table: dict[str, str]) -> bool:
    """True if `text` states `keyword` itself, an alias, or a close form of either."""
    words = _TOKEN.findall(text.lower())
    for spelling in spellings(keyword, aliases, table):
        if re.search(rf"(?<!\w){re.escape(spelling)}(?!\w)", text, re.IGNORECASE):
            return True
        if all(any(_close(t, w) for w in words) for t in _TOKEN.findall(spelling.lower())):
            return True
    return False


def validate(bank: dict, aliases: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Any error means the bank must not be used."""
    errors: list[str] = []
    warnings: list[str] = []
    meta = bank.get("meta") or {}
    voices = list(meta.get("voices") or [])
    max_chars = meta.get("max_chars")
    categories = {str(c).lower() for c in meta.get("category_keywords") or []}
    table = alias_table(aliases)
    seen: set[str] = set()

    def unique(item_id, kind: str) -> None:
        if not item_id:
            errors.append(f"{kind} without an id")
        elif item_id in seen:
            errors.append(f"duplicate id {item_id!r}")
        seen.add(item_id)

    if not voices:
        errors.append("meta.voices is empty")

    for section in bank.get("sections") or []:
        sid = section.get("id")
        unique(sid, "section")
        bullets = section.get("bullets") or []
        lo = section.get("min_bullets", 0)
        hi = section.get("max_bullets", len(bullets))
        if lo > hi or lo > len(bullets):
            errors.append(f"{sid}: min_bullets {lo} can't be met (max {hi}, {len(bullets)} bullets)")
        must = sum(1 for b in bullets if b.get("priority") == 1)
        if must > hi:
            errors.append(f"{sid}: {must} priority-1 bullets exceed max_bullets {hi}")
        # The title and stack line render next to every bullet, so a keyword
        # they state is not an overclaim.
        context = f"{section.get('title', '')} {section.get('stack', '')}"
        voice_counts = dict.fromkeys(voices, 0)

        for bullet in bullets:
            bid = bullet.get("id")
            unique(bid, f"{sid}: bullet")
            locked = {str(t).lower() for t in bullet.get("facts_locked") or []
                      if NUMBER.fullmatch(str(t))}
            missing = locked - numbers(bullet.get("fact", ""))
            if missing:
                errors.append(f"{bid}: facts_locked {sorted(missing)} not in its fact")
            variants = bullet.get("variants") or []
            if not variants:
                errors.append(f"{bid}: no variants")
            bullet_voices = set()

            for variant in variants:
                vid = variant.get("id")
                unique(vid, f"{bid}: variant")
                voice = variant.get("voice")
                if not voice:
                    errors.append(f"{vid}: missing voice")
                elif voice not in voices:
                    errors.append(f"{vid}: voice {voice!r} not in meta.voices {voices}")
                else:
                    bullet_voices.add(voice)

                text = variant.get("text") or ""
                if not text:
                    errors.append(f"{vid}: empty text")
                if max_chars and len(text) > max_chars:
                    errors.append(f"{vid}: {len(text)} chars, over max_chars {max_chars}")
                extra = numbers(text) - locked
                if extra:
                    errors.append(f"{vid}: numbers {sorted(extra)} not in facts_locked {sorted(locked)}")

                for keyword in variant.get("keywords") or []:
                    keyword = str(keyword)
                    if supported(keyword, f"{text} {context}", aliases, table):
                        continue
                    if canonical(keyword, table).lower() in categories:
                        warnings.append(f"{vid}: category keyword {keyword!r} not in text")
                    else:
                        errors.append(f"{vid}: keyword {keyword!r} not supported by its text")

            for voice in bullet_voices:
                voice_counts[voice] += 1

        # Selection uses one voice per resume, so a voice that covers only some
        # of a section's bullets could leave it unable to meet min_bullets.
        for voice, count in voice_counts.items():
            if 0 < count < len(bullets):
                errors.append(f"{sid}: voice {voice!r} covers {count}/{len(bullets)} bullets")

    return errors, warnings


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else BANK_PATH
    errors, warnings = validate(load_yaml(path), load_yaml(ALIASES_PATH))
    for w in warnings:
        print(f"WARN   {w}")
    for e in errors:
        print(f"ERROR  {e}")
    print(f"{path.name}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
