"""Which company ATS boards are worth polling directly? (Phase 0 of ATS_PLAN.md)

Runs each candidate board through the real pipeline (title screen, title
exclusions, location/sponsorship filter) and compares its EU matches with what
the Aramente source already delivers.

    python scripts/ats_coverage.py [candidates.yaml]     # default: scripts/ats_candidates.yaml

Columns:
  jobs      postings on the board
  eu        EU matches that would reach the channel
  us        US matches (known sponsors); the US repos may already carry these
  aramente  EU matches Aramente already delivers under the same ats_key
  new       EU matches Aramente lacks: the coverage gain
  week      EU matches first published in the last 7 days: the latency gain
  keyless   Aramente matches for this company with no ats_key. Each one is a
            role that could be notified twice if this board is polled.

READ-ONLY. Never writes state, never posts to Discord.
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ats import fetch_company, screen_titles  # noqa: E402
from fetch import fetch_source  # noqa: E402
from filter import filter_postings  # noqa: E402
from normalize import normalize_company  # noqa: E402


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8")) or {}


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "scripts" / "ats_candidates.yaml"
    candidates = yaml.safe_load(path.read_text(encoding="utf-8"))["companies"]

    sources = load("sources.yaml")
    settings, locations, sponsors = sources["settings"], load("eu_locations.yaml"), load("h1b_sponsors.yaml")
    by_name = {source["name"]: source for source in sources["sources"]}
    options = by_name["ats"]["options"]

    aramente = filter_postings(fetch_source(by_name["Aramente/eu-tech-jobs"]), locations, settings, sponsors)
    aramente_keys = {p["ats_key"] for p in aramente if p["ats_key"]}
    keyless = Counter(normalize_company(p["company"]) for p in aramente if not p["ats_key"])

    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y%m%d")
    with ThreadPoolExecutor(max_workers=8) as pool:
        boards = list(pool.map(fetch_company, candidates))

    rows = []
    for company, postings in zip(candidates, boards):
        matches = filter_postings(screen_titles(postings, options), locations, settings, sponsors)
        eu = [p for p in matches if p["match_reason"].startswith("EU")]
        have = sum(p["ats_key"] in aramente_keys for p in eu)
        # date_posted is MMDDYYYY; reorder to compare. Undated never counts as recent.
        week = sum(bool(d := p["date_posted"]) and d[4:] + d[:4] >= week_ago for p in eu)
        rows.append((company["name"], f"{company['ats']}/{company['board']}", len(postings),
                     len(eu), len(matches) - len(eu), have, len(eu) - have, week,
                     keyless[normalize_company(company["name"])]))

    rows.sort(key=lambda r: (r[6], r[3]), reverse=True)
    header = ("company", "board", "jobs", "eu", "us", "aramente", "new", "week", "keyless")
    print("  ".join(f"{h:>8}" if i > 1 else f"{h:<22}" for i, h in enumerate(header)))
    for row in rows:
        print("  ".join(f"{v:>8}" if i > 1 else f"{v:<22}" for i, v in enumerate(row)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
