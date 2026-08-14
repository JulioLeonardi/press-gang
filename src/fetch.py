"""Per-source adapters. Each returns a list of normalized posting dicts.

Normalized shape:
    id, company, title, location, url, sponsorship_flag, source_repo,
    date_posted (MMDDYYYY), active

Adapters never raise past `fetch_source`: a source that changes shape is
logged and skipped so one broken repo can't take down the run.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import requests

from normalize import make_id, normalize_text, to_mmddyyyy

log = logging.getLogger(__name__)

TIMEOUT = 30
HEADERS = {"User-Agent": "job-alert-bot (+github actions)"}

# Simplify's sponsorship enum -> our tri-state.
# "Other" is ~99% of rows and means "unspecified", not "offered".
_SPONSORSHIP_MAP = {
    "offers sponsorship": "yes",
    "does not offer sponsorship": "no",
    "u.s. citizenship is required": "no",
    "other": "unknown",
}

_CLOSED_MARKERS = ("\U0001F512", "🔒")  # padlock = closed application
_SPONSOR_MARKER = "\U0001F6C2"  # 🛂 = sponsorship available
_HREF = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"</?br\s*/?>", re.IGNORECASE)
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")


def fetch_source(source: dict) -> list[dict]:
    """Download and parse one configured source. Returns [] on any failure."""
    name = source.get("name", "<unnamed>")
    adapter = source.get("adapter")
    url = source.get("url")

    try:
        response = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("source %s: fetch failed (%s) -- skipping", name, exc)
        return []

    try:
        if adapter == "simplify_json":
            postings = _parse_simplify_json(response.text, name)
        elif adapter == "markdown_table":
            postings = _parse_markdown_table(response.text, name)
        else:
            log.warning("source %s: unknown adapter %r -- skipping", name, adapter)
            return []
    except Exception as exc:  # noqa: BLE001 - one bad source must not kill the run
        log.warning("source %s: parse failed (%s) -- skipping", name, exc)
        return []

    if not postings:
        log.warning("source %s: parsed 0 postings (format may have changed)", name)
    else:
        log.info("source %s: parsed %d postings", name, len(postings))
    return postings


def _parse_simplify_json(text: str, source_name: str) -> list[dict]:
    """Simplify-style listings.json: a flat list of posting objects.

    Real fields (verified against the live file): company_name, title,
    locations (a LIST, not a string), url, sponsorship (4-value enum),
    date_posted (unix epoch int), active, is_visible.
    """
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("expected a JSON list")

    postings = []
    for row in data:
        if not isinstance(row, dict):
            continue
        company = row.get("company_name") or ""
        title = row.get("title") or ""
        if not company or not title:
            continue
        if row.get("is_visible") is False:
            continue

        locations = row.get("locations") or []
        if isinstance(locations, str):
            locations = [locations]
        location = " | ".join(str(loc) for loc in locations) or "Unspecified"

        date_posted = to_mmddyyyy(row.get("date_posted"))
        sponsorship = _SPONSORSHIP_MAP.get(
            normalize_text(row.get("sponsorship")), "unknown"
        )

        postings.append(
            {
                "id": make_id(company, title, date_posted),
                "company": str(company).strip(),
                "title": str(title).strip(),
                "location": location,
                "url": row.get("url") or row.get("company_url") or "",
                "sponsorship_flag": sponsorship,
                "source_repo": source_name,
                "date_posted": date_posted,
                "active": bool(row.get("active", True)),
            }
        )
    return postings


def _parse_markdown_table(text: str, source_name: str) -> list[dict]:
    """README table: | Company | Role | Location | Application/Link | Date |

    Verified quirks in the live file: company wrapped in **bold**; multiple
    locations joined by </br>; the link cell is an <a href> around an image,
    or a bare 🔒 when the posting is closed; 🛂 in the role cell means
    sponsorship is offered; the date is 'Aug 05' with no year.
    """
    rows = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if not rows:
        raise ValueError("no table rows found")

    columns = None
    postings = []
    now = datetime.now(timezone.utc)

    for line in rows:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]

        # Header row: learn the column order instead of assuming it.
        lowered = [normalize_text(c) for c in cells]
        if "company" in lowered and any("role" in c or "position" in c for c in lowered):
            columns = _map_columns(lowered)
            continue
        if columns is None:
            continue
        if all(set(c) <= set("-: ") for c in cells):  # separator row
            continue
        if len(cells) <= max(columns.values()):
            continue

        company = _clean(cells[columns["company"]])
        title_raw = cells[columns["role"]]
        title = _clean(title_raw)
        if not company or not title:
            continue

        link_cell = cells[columns["link"]] if "link" in columns else ""
        # A padlock (and no link) means the application is closed.
        if any(marker in link_cell for marker in _CLOSED_MARKERS) and not _HREF.search(link_cell):
            continue

        url = ""
        href = _HREF.search(link_cell) or _HREF.search(title_raw)
        if href:
            url = href.group(1)
        else:
            md = _MD_LINK.search(link_cell) or _MD_LINK.search(title_raw)
            if md:
                url = md.group(2)

        location = _clean(_BR.sub(" | ", cells[columns["location"]])) if "location" in columns else ""
        date_raw = cells[columns["date"]] if "date" in columns else ""
        date_posted = to_mmddyyyy(_clean(date_raw), fallback=now)

        sponsorship = "yes" if _SPONSOR_MARKER in title_raw else "unknown"

        postings.append(
            {
                "id": make_id(company, title, date_posted),
                "company": company,
                "title": title,
                "location": location or "Unspecified",
                "url": url,
                "sponsorship_flag": sponsorship,
                "source_repo": source_name,
                "date_posted": date_posted,
                "active": True,
            }
        )
    return postings


def _map_columns(lowered_header: list[str]) -> dict[str, int]:
    """Locate columns by name so a reordered/renamed table still parses."""
    columns: dict[str, int] = {}
    for index, name in enumerate(lowered_header):
        if "company" in name and "company" not in columns:
            columns["company"] = index
        elif ("role" in name or "position" in name) and "role" not in columns:
            columns["role"] = index
        elif "location" in name and "location" not in columns:
            columns["location"] = index
        elif ("link" in name or "application" in name) and "link" not in columns:
            columns["link"] = index
        elif "date" in name and "date" not in columns:
            columns["date"] = index
    if "company" not in columns or "role" not in columns:
        raise ValueError(f"could not locate company/role columns in {lowered_header}")
    return columns


def _clean(cell: str) -> str:
    """Strip markdown bold, HTML tags, emoji, and the ↳ continuation arrow."""
    text = _BR.sub(" | ", cell)
    text = _HTML_TAG.sub("", text)
    text = text.replace("**", "").replace("↳", " ")
    text = _MD_LINK.sub(r"\1", text)
    text = _EMOJI.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" |")
