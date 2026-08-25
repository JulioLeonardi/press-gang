"""Render the live job board: one self-contained HTML page for GitHub Pages.

Runs the same fetch -> filter pipeline the bot uses, then writes every current
match into a static page grouped by sponsorship tier and posting date. Rebuilt
nightly; the page is a snapshot of what is open *now*, not an append-only log.

Two deliberate differences from what Discord receives:

  * `allow_unknown_sponsorship` is forced on, so the "sponsorship unverified"
    tier appears here even though the channel is configured to drop it. The
    board is where you go to look at the ones the bot deliberately withheld.
  * Nothing is filtered on `seen.json`. The page includes postings already
    notified about, because it is meant to be a browsable archive rather than
    a queue. `seen.json` is still read, but only to mark rows NEW.

READ-ONLY. Never writes state, never posts to Discord.

    python scripts/render_board.py -o site/index.html
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fetch import fetch_source  # noqa: E402
from filter import filter_postings  # noqa: E402

log = logging.getLogger("board")

TEMPLATE = Path(__file__).resolve().parent / "board_template.html"

# Order matters: it is both the tier-card order and the precedence used to
# assign a posting to exactly one group. Explicit sponsorship outranks the
# company-level guess; EU roles never need a sponsorship story at all.
# (key, card heading, card hint, short chip label)
TIERS = [
    ("confirmed", "Sponsorship confirmed", "The listing itself says so", "Confirmed"),
    ("eu", "EU location", "No visa question — UK excluded", "EU"),
    ("known", "Known H-1B employer", "Company files petitions (USCIS FY22–23)", "Known"),
    ("unverified", "Sponsorship unverified", "No signal either way — check before applying", "Unverified"),
]


def tier_of(posting: dict) -> str:
    if posting.get("sponsorship_flag") == "yes":
        return "confirmed"
    if str(posting.get("match_reason", "")).startswith("EU"):
        return "eu"
    if posting.get("known_sponsor"):
        return "known"
    return "unverified"


def iso_date(posting: dict) -> str:
    """MMDDYYYY -> YYYY-MM-DD. The template groups and sorts on this."""
    raw = str(posting.get("date_posted", ""))
    try:
        return datetime.strptime(raw, "%m%d%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_rows(postings: list[dict], seen: set[str], new_since: datetime | None) -> list[dict]:
    """Compact row dicts. Short keys because this ships inline in the page."""
    rows = []
    for posting in postings:
        date = iso_date(posting)
        # NEW = first seen by the bot within the window, i.e. it showed up in
        # Discord since the last build. Postings not in seen.json at all are
        # not marked: they are usually the unverified tier, which never went out.
        is_new = False
        if new_since is not None:
            meta = seen.get(posting["id"]) if isinstance(seen, dict) else None
            if meta:
                try:
                    first = datetime.fromisoformat(str(meta.get("first_seen", "")))
                    if first.tzinfo is None:
                        first = first.replace(tzinfo=timezone.utc)
                    is_new = first >= new_since
                except ValueError:
                    pass
        row = {
            "i": posting["id"],
            "c": str(posting.get("company", "")).strip(),
            "t": str(posting.get("title", "")).strip(),
            "l": str(posting.get("location", "")).strip() or "Unspecified",
            "u": posting.get("url") or "",
            "d": date,
            "g": tier_of(posting),
            "s": str(posting.get("source_repo", "")).split("/")[-1],
        }
        if is_new:
            row["n"] = 1
        rows.append(row)

    # Newest first, undated last. The template relies on this order to emit
    # day headings in a single pass.
    rows.sort(key=lambda r: (r["d"] or "0000-00-00"), reverse=True)
    return rows


def render(rows: list[dict], generated: datetime, title: str) -> str:
    counts = {key: sum(1 for r in rows if r["g"] == key) for key, *_ in TIERS}

    tiers_html, chips_html = [], []
    for key, label, hint, chip in TIERS:
        if not counts[key]:
            continue  # an empty tier is noise, not information
        tiers_html.append(
            f'<div class="tier t-{key}"><div class="n">{counts[key]}</div>'
            f'<div class="k">{esc(label)}</div><div class="h">{esc(hint)}</div></div>'
        )
        chips_html.append(
            f'<button class="chip t-{key}" data-g="{key}" aria-pressed="true">'
            f'<span class="dot"></span>{esc(chip)}'
            f'<span class="ct">{counts[key]}</span></button>'
        )

    newest = next((r["d"] for r in rows if r["d"]), "")
    standfirst = (
        f'<strong>{len(rows)}</strong> postings currently open that pass the screening rules — '
        "internships, cleared roles and non-CS families removed. "
        f'Rebuilt nightly; last run {generated:%B %d, %Y at %H:%M} UTC.'
    )

    html = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "__TITLE__": esc(title),
        "__EYEBROW__": esc(
            f"Live board · rebuilt {generated:%B %d, %Y}"
            + (f" · newest posting {newest}" if newest else "")
        ),
        "__STANDFIRST__": standfirst,
        "__TIERS__": "\n      ".join(tiers_html),
        "__CHIPS__": "\n      " + "\n      ".join(chips_html),
        "__ACTIVE__": json.dumps({key: True for key, *_ in TIERS}),
        # Inline JSON inside a <script> block: the only sequence that can break
        # out is a literal "</script>", so neutralise the slash.
        "__DATA__": json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
                        .replace("</", "<\\/"),
    }
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)
    return html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default="site/index.html")
    parser.add_argument("--title", default="Job Board")
    parser.add_argument("--max-age-days", type=int, default=60,
                        help="drop postings whose date is older than this (0 = keep all)")
    parser.add_argument("--new-window-hours", type=int, default=24,
                        help="mark rows NEW if the bot first saw them this recently")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s", stream=sys.stdout)

    sources_config = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    locations_config = yaml.safe_load((ROOT / "config" / "eu_locations.yaml").read_text(encoding="utf-8"))
    sponsors_path = ROOT / "config" / "h1b_sponsors.yaml"
    sponsors_config = (yaml.safe_load(sponsors_path.read_text(encoding="utf-8")) or {}) \
        if sponsors_path.exists() else {}

    # The board shows the unverified tier even though the channel doesn't.
    settings = dict(sources_config.get("settings", {}) or {})
    settings["allow_unknown_sponsorship"] = True

    state_path = ROOT / "state" / "seen.json"
    seen: dict = {}
    if state_path.exists():
        try:
            seen = json.loads(state_path.read_text(encoding="utf-8")).get("postings", {})
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("seen.json unreadable (%s) -- no NEW badges this build", exc)

    postings = []
    for source in sources_config.get("sources", []):
        postings.extend(fetch_source(source))
    if not postings:
        # Refuse to overwrite a good page with an empty one just because every
        # source happened to be down.
        log.error("no postings fetched -- leaving the existing page untouched")
        return 1

    matches = filter_postings(postings, locations_config, settings, sponsors_config)

    by_id: dict[str, dict] = {}
    for posting in matches:
        by_id.setdefault(posting["id"], posting)

    selected = list(by_id.values())
    if args.max_age_days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.max_age_days)).strftime("%Y-%m-%d")
        before = len(selected)
        selected = [p for p in selected if (iso_date(p) or "") >= cutoff]
        log.info("age filter (%dd): kept %d of %d", args.max_age_days, len(selected), before)

    generated = datetime.now(timezone.utc)
    new_since = generated - timedelta(hours=args.new_window_hours) \
        if args.new_window_hours > 0 else None
    rows = build_rows(selected, seen, new_since)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, generated, args.title), encoding="utf-8")

    tiers = {key: sum(1 for r in rows if r["g"] == key) for key, *_ in TIERS}
    log.info("wrote %s -- %d postings (%s), %d marked NEW, %.0fKB",
             out, len(rows), ", ".join(f"{k}={v}" for k, v in tiers.items()),
             sum(1 for r in rows if r.get("n")), out.stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
