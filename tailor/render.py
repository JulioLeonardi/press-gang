"""Layout -> one-page PDF, refusing any bullet that isn't verbatim from the bank.

Run: python -m tailor.render [posting_id]
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from tailor.bank import ALIASES_PATH, BANK_PATH, ROOT, load_yaml
from tailor.validate import validate

TEMPLATE_PATH = ROOT / "resume" / "template.tex"
OUT_DIR = ROOT / "out"
SECTION_HEADINGS = {"experience": "Experience", "project": "Projects", "involvement": "Involvement"}
_TEX_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        # "~400%" wants a mid-height tilde. Computer Modern puts \textasciitilde
    # near cap height and \texttildelow on the baseline; $\sim$ looks right but
    # its math box adds enough line height to push this full page onto a second.
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\raisebox{0.5ex}{\texttildelow}",
    "^": r"\textasciicircum{}", "–": "--",
}


class VerbatimError(ValueError):
    """A bullet about to be rendered is not byte-identical to its bank text."""


class PageOverflow(RuntimeError):
    """The rendered resume is longer than one page."""


def tex(text) -> str:
    return "".join(_TEX_ESCAPES.get(c, c) for c in str(text))


def _href(url: str, inner: str) -> str:
    return r"\href{" + url.replace("%", r"\%").replace("#", r"\#") + "}{" + inner + "}"


def full_layout(bank: dict, voice: str = "technical") -> list[dict]:
    """Stand-in until select.py exists: every priority-1 section, all bullets."""
    layout = []
    for section in bank["sections"]:
        if section.get("priority") != 1:
            continue
        bullets = []
        for bullet in section["bullets"]:
            variant = next(v for v in bullet["variants"] if v["voice"] == voice)
            bullets.append({"variant_id": variant["id"], "text": variant["text"]})
        layout.append({"section_id": section["id"], "bullets": bullets})
    return layout


def assert_verbatim(layout: list[dict], bank: dict) -> None:
    texts = {v["id"]: v["text"]
             for s in bank["sections"] for b in s["bullets"] for v in b["variants"]}
    for entry in layout:
        for bullet in entry["bullets"]:
            if texts.get(bullet["variant_id"]) != bullet["text"]:
                raise VerbatimError(f"{bullet['variant_id']}: text is not verbatim from the bank: "
                                    f"{bullet['text']!r}")


def _header(header: dict) -> list[str]:
    parts = [r"\small {" + tex(header["phone"]) + "}",
             _href("mailto:" + header["email"], r"\underline{" + tex(header["email"]) + "}")]
    parts += [_href(link["url"], r"\underline{" + tex(link["label"]) + "}")
              for link in header.get("links") or []]
    return [r"\begin{center}",
            r"    \textbf{\Huge \scshape " + tex(header["name"]) + r" \\ \vspace{1pt}}",
            "    " + " $|$\n    ".join(parts),
            r"\end{center}", ""]


def _education(education: list[dict]) -> list[str]:
    lines = [r"\section{Education}", r"  \resumeSubHeadingListStart"]
    for school in education:
        lines += [r"    \resumeSubheading",
                  "      {" + tex(school["school"]) + "}{" + tex(school["dates"]) + "}",
                  "      {" + " $|$ ".join(tex(p) for p in school["degree"].split(" | "))
                  + "}{" + tex(school.get("gpa", "")) + "}"]
    return lines + [r"  \resumeSubHeadingListEnd", ""]


def _skills(skills: list[dict]) -> list[str]:
    lines = [r"\section{Technical Skills}",
             r" \begin{itemize}[leftmargin=0.15in, label={}]",
             r"    \small{\item{"]
    lines += ["     \\textbf{" + tex(s["label"]) + "}{: " + tex(s["items"]) + r"} \\" for s in skills]
    return lines + [r"    }}", r"\end{itemize}", ""]


def _entry(section: dict, bullets: list[dict]) -> list[str]:
    title = tex(section["title"])
    if section["kind"] == "project":
        name = r"\textbf{\underline{" + title + "}}" if section.get("url") else r"\textbf{" + title + "}"
        if section.get("url"):
            name = _href(section["url"], name)
        if section.get("stack"):
            name += r" $|$ \emph{" + tex(section["stack"]) + "}"
        lines = [r"      \resumeProjectHeading",
                 "          {" + name + "}{" + tex(section["dates"]) + "}"]
    else:
        if section.get("url"):
            title = _href(section["url"], r"\underline{" + title + "}")
        org = tex(section.get("org", ""))
        if section.get("org_url"):
            org = _href(section["org_url"], r"\underline{" + org + "}")
        lines = [r"      \resumeSubheading",
                 "          {" + title + "}{" + tex(section["dates"]) + "}",
                 "          {" + org + "}{" + tex(section.get("location", "")) + "}"]
    lines.append(r"          \resumeItemListStart")
    lines += [r"            \resumeItem{" + tex(b["text"]) + "}" for b in bullets]
    return lines + [r"          \resumeItemListEnd", ""]


def _awards(awards: list[dict]) -> list[str]:
    lines = [r"\section{Awards}", r"  \resumeSubHeadingListStart"]
    for award in awards:
        lines += [r"    \resumeProjectHeading",
                  r"          {\textbf{" + tex(award["name"]) + "}{}}{" + tex(award["date"]) + "}"]
    return lines + [r"  \resumeSubHeadingListEnd", ""]


def build_tex(bank: dict, layout: list[dict]) -> str:
    sections = {s["id"]: s for s in bank["sections"]}
    body = _header(bank["header"]) + _education(bank["education"]) + _skills(bank["skills"])
    for kind, heading in SECTION_HEADINGS.items():
        entries = [e for e in layout if sections[e["section_id"]]["kind"] == kind]
        if not entries:
            continue
        body += [r"\section{" + heading + "}", r"    \resumeSubHeadingListStart", ""]
        for entry in entries:
            body += _entry(sections[entry["section_id"]], entry["bullets"])
        body += [r"    \resumeSubHeadingListEnd", ""]
    body += _awards(bank.get("awards") or [])
    return TEMPLATE_PATH.read_text(encoding="utf-8").replace("%%BODY%%", "\n".join(body))


def compile_pdf(source: str, workdir: Path) -> tuple[Path, int]:
    """Run pdflatex; return the PDF path and its page count."""
    (workdir / "resume.tex").write_text(source, encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "resume.tex"],
                   cwd=workdir, capture_output=True, timeout=120)
    log = (workdir / "resume.log").read_text(encoding="utf-8", errors="replace")
    written = re.search(r"Output written on resume\.pdf \((\d+) pages?", log)
    if not written:
        errors = [line for line in log.splitlines() if line.startswith("!")]
        raise RuntimeError(f"pdflatex failed: {errors or 'see resume.log'}")
    return workdir / "resume.pdf", int(written.group(1))


def render(bank: dict, layout: list[dict], out_dir: Path, voice: str) -> Path:
    """Write the PDF and selection.json to `out_dir`; return the PDF path."""
    assert_verbatim(layout, bank)
    with tempfile.TemporaryDirectory() as tmp:
        pdf, pages = compile_pdf(build_tex(bank, layout), Path(tmp))
        if pages != 1:
            raise PageOverflow(f"resume is {pages} pages")
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (bank["header"]["name"].replace(" ", "_") + "_Resume.pdf")
        shutil.copyfile(pdf, target)
    selection = {
        "bank_sha256": hashlib.sha256(yaml.safe_dump(bank, sort_keys=True).encode()).hexdigest(),
        "voice": voice,
        "sections": [{"section_id": e["section_id"],
                      "variant_ids": [b["variant_id"] for b in e["bullets"]]} for e in layout],
    }
    (out_dir / "selection.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
    return target


def main(argv: list[str]) -> int:
    bank = load_yaml(BANK_PATH)
    errors, _ = validate(bank, load_yaml(ALIASES_PATH))
    if errors:
        print("bank has errors; run python -m tailor.validate")
        return 1
    posting_id = argv[0] if argv else "base"
    print(render(bank, full_layout(bank), OUT_DIR / posting_id, "technical"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
