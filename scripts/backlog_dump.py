"""One-off: dump already-seen postings that pass the CURRENT screening rules.

Why this exists: state was seeded (and later re-seeded) while the filters were
different, so a pile of postings were silently marked seen and never sent to
Discord. This re-runs today's rules over the live feeds and reports everything
already in seen.json from a cutoff date onward, so the backlog can be read in
one sitting.

seen.json stores only {first_seen, company, title} -- no location, URL, or
sponsorship flag -- so the postings have to be re-fetched from the sources and
matched back by id. Anything the sources have since dropped (closed reqs) is
simply gone and cannot be recovered; the run reports how many that was.

READ-ONLY. Never writes state, never posts to Discord.

    python scripts/backlog_dump.py --since 07312026 -o backlog.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fetch import fetch_source  # noqa: E402
from filter import filter_postings  # noqa: E402

log = logging.getLogger("backlog")


def parse_cutoff(text: str) -> datetime:
    """Accept MMDDYYYY (the key format) or YYYY-MM-DD (what people type)."""
    for fmt in ("%m%d%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"unparseable --since {text!r}; use MMDDYYYY or YYYY-MM-DD")


def posted_at(posting: dict) -> datetime | None:
    raw = str(posting.get("date_posted", ""))
    try:
        return datetime.strptime(raw, "%m%d%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def render(postings: list[dict], cutoff: datetime, missing: int) -> str:
    """Markdown grouped by sponsorship confidence, newest first within a group."""
    badged = [p for p in postings if p.get("known_sponsor")]
    explicit = [p for p in postings if p.get("sponsorship_flag") == "yes"]
    eu = [p for p in postings if str(p.get("match_reason", "")).startswith("EU")]
    unverified = [p for p in postings if p.get("unverified")]

    lines = [
        f"# Backlog since {cutoff:%B %-d, %Y}" if sys.platform != "win32"
        else f"# Backlog since {cutoff:%B %d, %Y}",
        "",
        f"{len(postings)} postings already in `seen.json` that pass current screening.",
        "",
        f"- 🛂 Explicit sponsorship: **{len(explicit)}**",
        f"- 🛂 Known H-1B employer: **{len(badged)}**",
        f"- 🇪🇺 EU location: **{len(eu)}**",
        f"- ⚠️ Sponsorship unverified: **{len(unverified)}**",
        "",
    ]
    if missing:
        lines += [
            f"> {missing} other ids in `seen.json` are not in the live feeds and "
            "could not be re-screened. These are overwhelmingly closed or filled "
            "reqs -- normal churn, not lost matches. (`first_seen` is when the "
            "bot noticed a posting, not when it was published, so this count "
            "cannot be narrowed to the cutoff window.)",
            "",
        ]

    groups = [
        ("Explicit sponsorship", explicit),
        ("EU location", eu),
        ("Known H-1B employer", badged),
        ("Sponsorship unverified", unverified),
    ]
    emitted: set[str] = set()
    for heading, group in groups:
        rows = [p for p in group if p["id"] not in emitted]
        if not rows:
            continue
        emitted.update(p["id"] for p in rows)
        lines += [f"## {heading} ({len(rows)})", ""]
        lines += ["| Posted | Company | Role | Location | Link |",
                  "|---|---|---|---|---|"]
        for p in sorted(rows, key=posted_at, reverse=True):
            d = posted_at(p)
            date = f"{d:%m/%d}" if d else "?"
            url = p.get("url") or ""
            link = f"[apply]({url})" if url.startswith("http") else "—"
            # Pipes inside cells would break the table.
            company = str(p.get("company", "")).replace("|", "/")
            title = str(p.get("title", "")).replace("|", "/")
            location = str(p.get("location", "")).replace("|", " / ")
            lines.append(f"| {date} | {company} | {title} | {location} | {link} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="07312026",
                        help="cutoff, inclusive: MMDDYYYY or YYYY-MM-DD (default 07312026)")
    parser.add_argument("-o", "--output", default="backlog.md")
    parser.add_argument("--include-unseen", action="store_true",
                        help="also include matches NOT yet in seen.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stdout)
    cutoff = parse_cutoff(args.since)

    sources_config = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    locations_config = yaml.safe_load((ROOT / "config" / "eu_locations.yaml").read_text(encoding="utf-8"))
    sponsors_config = yaml.safe_load((ROOT / "config" / "h1b_sponsors.yaml").read_text(encoding="utf-8")) or {}

    # The whole point of the catch-up: unverified US roles included.
    settings = dict(sources_config.get("settings", {}) or {})
    settings["allow_unknown_sponsorship"] = True

    state = json.loads((ROOT / "state" / "seen.json").read_text(encoding="utf-8"))
    seen = set(state.get("postings", state if isinstance(state, dict) else {}))
    log.info("seen.json tracks %d ids", len(seen))

    postings = []
    for source in sources_config.get("sources", []):
        postings.extend(fetch_source(source))
    if not postings:
        log.error("no postings fetched -- nothing to report")
        return 1

    matches = filter_postings(postings, locations_config, settings, sponsors_config)

    # Dedupe before the window test: the same id can arrive from two repos.
    by_id: dict[str, dict] = {}
    for posting in matches:
        by_id.setdefault(posting["id"], posting)

    in_window = [p for p in by_id.values()
                 if (d := posted_at(p)) is not None and d >= cutoff]

    selected = [p for p in in_window if args.include_unseen or p["id"] in seen]
    unseen = sum(1 for p in in_window if p["id"] not in seen)

    # Seen ids the live feeds no longer carry at all. Can't be narrowed to the
    # cutoff window: state records first_seen, not the posting's publish date.
    missing = sum(1 for pid in seen if pid not in by_id)

    log.info("%d matches in window, %d selected (%d unseen %s)",
             len(in_window), len(selected), unseen,
             "included" if args.include_unseen else "excluded")

    out = Path(args.output)
    if out.suffix == ".json":
        # For feeding a viewer; the markdown is for reading directly.
        out.write_text(
            json.dumps(sorted(selected, key=lambda p: p.get("date_posted", ""), reverse=True),
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        out.write_text(render(selected, cutoff, missing), encoding="utf-8")
    log.info("wrote %s (%d postings)", out, len(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
