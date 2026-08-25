"""Per-source adapters. Each returns a list of normalized posting dicts.

Normalized shape:
    id, company, title, location, url, sponsorship_flag, source_repo,
    date_posted (MMDDYYYY), active

Adapters never raise past `fetch_source`: a source that changes shape is
logged and skipped so one broken repo can't take down the run.
"""

from __future__ import annotations

import io
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

    try:
        if adapter == "eu_parquet":
            # Owns its own I/O: two files, and column-pruned range reads.
            postings = _fetch_eu_parquet(source, name)
        elif adapter in _TEXT_ADAPTERS:
            response = requests.get(source.get("url"), timeout=TIMEOUT, headers=HEADERS)
            response.raise_for_status()
            postings = _TEXT_ADAPTERS[adapter](response.text, name)
        else:
            log.warning("source %s: unknown adapter %r -- skipping", name, adapter)
            return []
    except requests.RequestException as exc:
        log.warning("source %s: fetch failed (%s) -- skipping", name, exc)
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


# --- eu_parquet -------------------------------------------------------------
#
# Aramente/eu-tech-jobs publishes a daily Parquet snapshot of every *active*
# job it tracks, not a curated new-grad list. Three things follow from that:
#
#   1. The file is ~19MB, but 17.8MB of it is the `description_md` column,
#      which we never display. We read only the columns we need over HTTP
#      Range requests (~1.4MB, ~13 requests) instead of downloading the lot.
#      The file is a single row group, so column pruning is the only lever;
#      there are no row groups to skip.
#   2. It ships a stable per-job id (sha256(slug + url)[:16]) and we use it
#      instead of make_id(). See _EU_ID_NOTE below.
#   3. It is a firehose (~20k live rows, ~180 new/day past the EU filter), so
#      the role/seniority narrowing in sources.yaml is load-bearing, not
#      cosmetic. Without it this one source outvotes the other two ~5:1.

_EU_ID_NOTE = """\
Unlike the other adapters this one keys on the source's own id rather than
make_id(company, title, date). Two measurements drove that:

  * `posted_at` is not stable. Comparing the 2026-08-18 and 2026-08-24
    snapshots, 207 of 16,977 jobs present in both (1.2%) had their
    posted_at bumped forward -- welcometothejungle re-dates listings it
    re-promotes. Under make_id every bump mints a new id and re-notifies a
    job already sent: ~24/day of pure duplicates.
  * make_id's whole reason for existing is cross-source dedupe, and here
    there is nothing to dedupe against. Running both snapshots and all
    three sources through filter_postings, the overlap between this source
    and SimplifyJobs/vanshb03 was exactly 0 postings -- unsurprising, since
    those two are US new-grad lists and this one is EU-only.

So the source id costs nothing and removes the duplicates. If a US-and-EU
source is ever added, revisit this."""

_EU_JOB_COLUMNS = [
    "id", "company_slug", "title", "url", "location",
    "seniority", "role_family", "posted_at",
]
_EU_COMPANY_COLUMNS = ["slug", "name", "industry_tags"]

# Fallback if sources.yaml doesn't override it. Matched whole-word against the
# normalized title, and only consulted for rows the upstream tagger has NOT
# assigned a seniority -- which is ~75% of rows, and nearly 100% of brand-new
# ones, because the tagger runs behind the scraper.
_EU_SENIOR_TITLE_PATTERNS = [
    "senior", "sr", "staff", "principal", "lead", "head",
    "director", "vp", "chief", "architect", "manager",
]


class _HttpRangeFile(io.RawIOBase):
    """Minimal seekable read-only file over HTTP Range requests.

    Exists so pyarrow can pull individual column chunks out of a remote
    Parquet file without downloading the whole thing. Only the handful of
    methods pyarrow actually calls are implemented.
    """

    def __init__(self, url: str, session: requests.Session):
        self.url = url
        self.session = session
        self.pos = 0
        self.bytes_read = 0
        self.requests_made = 0
        head = session.head(url, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
        head.raise_for_status()
        try:
            self.size = int(head.headers["Content-Length"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"no usable Content-Length for {url}") from exc

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self.size - self.pos
        size = min(size, self.size - self.pos)
        if size <= 0:
            return b""
        start, end = self.pos, self.pos + size - 1
        response = self.session.get(
            self.url,
            headers={**HEADERS, "Range": f"bytes={start}-{end}"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.content
        # A server that ignores Range answers 200 with the entire file; slice
        # it ourselves rather than handing pyarrow bytes from the wrong offset.
        if response.status_code == 200 and len(data) > size:
            data = data[start : end + 1]
        self.requests_made += 1
        self.bytes_read += len(data)
        self.pos += len(data)
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def _fetch_eu_parquet(source: dict, source_name: str) -> list[dict]:
    """Aramente/eu-tech-jobs: latest/jobs.parquet + latest/companies.parquet."""
    # Imported lazily so a missing/broken pyarrow degrades to "this one source
    # is skipped" via fetch_source's handler, rather than an import error that
    # takes the whole run down with it.
    import pyarrow.parquet as pq

    session = requests.Session()
    companies = _load_eu_companies(source, session, pq)

    handle = _HttpRangeFile(source["url"], session)
    table = pq.ParquetFile(handle).read(columns=_EU_JOB_COLUMNS)
    log.debug(
        "source %s: read %d rows in %.2fMB over %d range requests (file is %.1fMB)",
        source_name, table.num_rows, handle.bytes_read / 1e6,
        handle.requests_made, handle.size / 1e6,
    )
    return eu_rows_to_postings(
        table.to_pylist(), companies, source.get("options") or {}, source_name
    )


def eu_rows_to_postings(
    rows: list[dict], companies: dict[str, str], options: dict, source_name: str
) -> list[dict]:
    """Row dicts + slug->name map -> normalized postings. Pure; no I/O."""
    role_families = set(options.get("role_families") or [])
    exclude_seniority = set(options.get("exclude_seniority") or [])
    senior_title = _compile_senior_title(options)

    postings: list[dict] = []
    dropped = {"company": 0, "role": 0, "seniority": 0, "senior_title": 0}

    for row in rows:
        company = companies.get(row["company_slug"] or "")
        if company is None:
            dropped["company"] += 1
            continue

        # An untagged row (role_family/seniority None) is kept: the upstream
        # tagger lags the scraper, so requiring a tag would systematically
        # discard exactly the newest postings -- the ones we exist to catch.
        role_family = row["role_family"]
        if role_families and role_family is not None and role_family not in role_families:
            dropped["role"] += 1
            continue

        seniority = row["seniority"]
        title = (row["title"] or "").strip()
        if seniority is not None:
            if seniority in exclude_seniority:
                dropped["seniority"] += 1
                continue
        elif senior_title and senior_title.search(normalize_text(title)):
            dropped["senior_title"] += 1
            continue

        if not title:
            dropped["company"] += 1
            continue

        # pyarrow hands back a datetime; tolerate a plain string too.
        posted_at = row.get("posted_at")
        if isinstance(posted_at, datetime):
            posted_at = posted_at.isoformat()
        date_posted = to_mmddyyyy(posted_at)

        postings.append(
            {
                # Source-native id on purpose -- see _EU_ID_NOTE.
                "id": row["id"],
                "company": company,
                "title": title,
                "location": (row["location"] or "").strip() or "Unspecified",
                "url": row["url"] or "",
                # The schema has a visa_sponsorship column but it is 100% null
                # (checked across the full 20,120-row snapshot). Every row is
                # therefore "unknown", which is harmless: these are EU-located
                # roles and pass filter_postings on location, not sponsorship.
                "sponsorship_flag": "unknown",
                "source_repo": source_name,
                "date_posted": date_posted,
                # latest/jobs.parquet is by definition the live snapshot --
                # a filled or withdrawn req is absent, not marked inactive.
                "active": True,
            }
        )

    log.debug(
        "source %s: dropped %d (no/hidden company=%d, role_family=%d, "
        "seniority=%d, senior-sounding title=%d)",
        source_name, sum(dropped.values()), dropped["company"],
        dropped["role"], dropped["seniority"], dropped["senior_title"],
    )
    return postings


def _load_eu_companies(source: dict, session: requests.Session, pq) -> dict[str, str]:
    """slug -> display name, for companies we're willing to surface.

    jobs.parquet only carries `company_slug` ("wttj-capgemini"), which is both
    ugly in an embed and useless to the H-1B sponsor matcher. companies.parquet
    is only ~78KB, so it's a plain GET.

    Three classes of slug are dropped here, mirroring what the project's own
    site does before rendering:
      - "via-*", stubs invented by aggregators with no real company behind them
      - slugs whose name is just the slug echoed back (nothing was resolved)
      - consumer fashion/beauty/luxury brands, which the upstream repo carries
        for a separate private landing page. They are ~400 of 1,732 companies
        and ~1,750 live jobs, mostly Paris-based, so they sail through the EU
        location filter and would otherwise dominate the channel.
    """
    url = source.get("companies_url")
    if not url:
        raise ValueError("eu_parquet source needs a `companies_url`")

    response = session.get(url, timeout=TIMEOUT, headers=HEADERS)
    response.raise_for_status()
    table = pq.read_table(io.BytesIO(response.content), columns=_EU_COMPANY_COLUMNS)

    options = source.get("options") or {}
    excluded_tags = {normalize_text(t) for t in options.get("exclude_industry_tags") or []}

    companies: dict[str, str] = {}
    for row in table.to_pylist():
        slug = row["slug"] or ""
        name = (row["name"] or "").strip()
        if not slug or not name or name == slug or slug.startswith("via-"):
            continue
        tags = {normalize_text(t) for t in row["industry_tags"] or []}
        if tags & excluded_tags:
            continue
        companies[slug] = name
    return companies


def _compile_senior_title(options: dict):
    patterns = options.get("senior_title_patterns")
    if patterns is None:
        patterns = _EU_SENIOR_TITLE_PATTERNS
    terms = [normalize_text(p) for p in patterns if p]
    if not terms:
        return None
    # Whole-word, same reasoning as compile_title_exclusions: a substring test
    # on "sr" hits "Ambassador" and one on "lead" hits "Leader"/"Lead Gen".
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b")


_TEXT_ADAPTERS = {
    "simplify_json": _parse_simplify_json,
    "markdown_table": _parse_markdown_table,
}
