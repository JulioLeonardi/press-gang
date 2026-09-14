"""Direct polling of company ATS job boards listed in config/companies.yaml.

Aramente already scrapes most of these boards once a day. We poll some of them
directly for the companies it doesn't track, and to hear about new roles within
the hour instead of the next morning. Every posting carries the ATS's own job id
as `ats_key`, which is what stops a role from being notified once from here and
again from Aramente. See ATS_PLAN.md for the measurements behind the company list.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import yaml

from fetch import HEADERS, TIMEOUT, _compile_required_title, _compile_senior_title
from normalize import normalize_text, to_mmddyyyy

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{board}",
    "lever": "https://api.lever.co/v0/postings/{board}?mode=json",
}


def fetch_ats(source: dict) -> list[dict]:
    """Every configured board, fetched in parallel, then title-screened."""
    path = ROOT / source["companies"]
    companies = yaml.safe_load(path.read_text(encoding="utf-8"))["companies"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        boards = list(pool.map(fetch_company, companies))
    return screen_titles([p for board in boards for p in board], source.get("options") or {})


def fetch_company(company: dict) -> list[dict]:
    """One board -> postings. A failing board yields [] and never stops the others.

    Logged with the company name because the likeliest failure is a 404 from a
    company that moved ATS, and that should be noticed rather than silently
    losing the company.
    """
    ats, board, name = company["ats"], company["board"], company["name"]
    try:
        response = requests.get(_ENDPOINTS[ats].format(board=board), timeout=TIMEOUT, headers=HEADERS)
        response.raise_for_status()
        postings = _PARSERS[ats](response.json(), name, board)
    except Exception as exc:  # noqa: BLE001 - one bad board must not kill the run
        log.warning("ats %s/%s (%s): %s -- skipping", ats, board, name, exc)
        return []
    if not postings:
        # Lever answers 200 [] for a board that exists but is empty, which is
        # also what a dead slug can look like. Keep it visible in the log.
        log.info("ats %s/%s (%s): board is empty", ats, board, name)
    return postings


def screen_titles(postings: list[dict], options: dict) -> list[dict]:
    """The same CS-title screen and senior-title guard the Aramente source uses.

    A company board lists every role the company has (Adyen's includes Account
    Managers in Kuala Lumpur), so without this the channel fills with sales and
    ops roles. There are no seniority tags here, so the title guard always applies.
    """
    required = _compile_required_title(options)
    senior = _compile_senior_title(options)
    kept = []
    for posting in postings:
        title = normalize_text(posting["title"])
        if senior and senior.search(title):
            continue
        if required and not required.search(title):
            continue
        kept.append(posting)
    log.info("ats: kept %d of %d postings after the title screen", len(kept), len(postings))
    return kept


def _posting(ats: str, board: str, company: str, key: str, title: str,
             locations: list, url: str, published) -> dict:
    # Some boards join several locations with ";" in one string
    # ("Germany (Remote) ; United States (Remote)"). filter_postings splits on
    # "|" and checks US first, so an unsplit string with a US part would never
    # match on its EU parts.
    parts = [part.strip() for loc in locations if loc for part in str(loc).split(";")]
    return {
        # Keyed on the ATS id for the reason _EU_ID_NOTE gives: it is stable,
        # and cross-source matching happens through ats_key, not the id.
        "id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:16],
        "company": company,
        "title": (title or "").strip(),
        "location": " | ".join(p for p in parts if p) or "Unspecified",
        "url": url or "",
        "sponsorship_flag": "unknown",
        "source_repo": f"ats/{ats}",
        "date_posted": to_mmddyyyy(published),
        "active": True,
        "ats_key": key,
        "seed_group": f"ats/{ats}/{board}",
    }


def _greenhouse(data: dict, company: str, board: str) -> list[dict]:
    # first_published, not updated_at: updated_at moves whenever the req is edited.
    return [
        _posting("greenhouse", board, company, f"gh:{job['id']}", job["title"],
                 [(job.get("location") or {}).get("name")], job["absolute_url"],
                 job.get("first_published"))
        for job in data["jobs"]
    ]


def _ashby(data: dict, company: str, board: str) -> list[dict]:
    # isListed: false is an unpublished role that the API still returns.
    return [
        _posting("ashby", board, company, f"ashby:{job['id'].lower()}", job["title"],
                 [job.get("location")] + [s.get("location") for s in job.get("secondaryLocations") or []],
                 job["jobUrl"], job.get("publishedAt"))
        for job in data["jobs"]
        if job.get("isListed", True)
    ]


def _lever(data: list, company: str, board: str) -> list[dict]:
    # createdAt is a millisecond epoch, which to_mmddyyyy already handles.
    return [
        _posting("lever", board, company, f"lever:{job['id'].lower()}", job["text"],
                 job["categories"].get("allLocations") or [job["categories"].get("location")],
                 job["hostedUrl"], job.get("createdAt"))
        for job in data
    ]


_PARSERS = {
    "greenhouse": _greenhouse,
    "ashby": _ashby,
    "lever": _lever,
}
