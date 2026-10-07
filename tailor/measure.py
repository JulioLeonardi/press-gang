"""How many lines each bullet text wraps to in the template, measured by pdflatex.

Character counts can't predict wrapping (a 110-char bullet can wrap where a
112-char one doesn't), so each text is typeset in the same list nesting and
font as a real \\resumeItem and TeX reports \\prevgraf, the paragraph's line
count. Results are cached per (template, text), so only new wordings cost a
pdflatex run.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from tailor.render import OUT_DIR, TEMPLATE_PATH, tex

CACHE_PATH = OUT_DIR / "line_cache.json"


def _key(template: str, text: str) -> str:
    return hashlib.sha256((template + "\0" + text).encode()).hexdigest()


def _typeset(template: str, texts: list[str]) -> list[int]:
    # Same nesting as a rendered entry (\resumeSubHeadingListStart > \item >
    # \resumeItemListStart > \resumeItem), so \linewidth matches. The text is
    # set in a box of that width because list items hide \prevgraf.
    body = [r"\begin{itemize}[leftmargin=0.15in, label={}]", r"\item", r"\begin{itemize}"]
    # Inside the box: \resumeItem's body verbatim, since the space before its
    # \vspace can push a full line over.
    body += [r"\item\setbox0\vbox{\hsize=\linewidth\noindent\small{ {" + tex(t) + r" \vspace{-2pt}} }"
             + r"\par\xdef\tailorlines{\the\prevgraf}}\typeout{LINES " + str(i) + r" \tailorlines}"
             for i, t in enumerate(texts)]
    body += [r"\end{itemize}", r"\end{itemize}"]
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "measure.tex").write_text(template.replace("%%BODY%%", "\n".join(body)), encoding="utf-8")
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "measure.tex"],
                       cwd=tmp, capture_output=True, timeout=120)
        log = (Path(tmp) / "measure.log").read_text(encoding="utf-8", errors="replace")
    found = {int(i): int(n) for i, n in re.findall(r"^LINES (\d+) (\d+)$", log, re.MULTILINE)}
    if len(found) != len(texts):
        raise RuntimeError("pdflatex failed while measuring bullets; see measure.log")
    return [found[i] for i in range(len(texts))]


def line_counts(bank: dict) -> dict[str, int] | None:
    """Text -> rendered line count for every variant, or None without pdflatex."""
    if not shutil.which("pdflatex"):
        return None
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    texts = sorted({v["text"] for s in bank["sections"] for b in s["bullets"] for v in b["variants"]})
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    missing = [t for t in texts if _key(template, t) not in cache]
    if missing:
        cache.update({_key(template, t): n for t, n in zip(missing, _typeset(template, missing))})
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=0, sort_keys=True), encoding="utf-8")
    return {t: cache[_key(template, t)] for t in texts}
