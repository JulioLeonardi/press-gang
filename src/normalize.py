"""Text normalization, date coercion, and dedupe-key construction."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# Company suffixes stripped before hashing so "Acme" and "Acme, Inc." collapse.
_COMPANY_SUFFIXES = re.compile(
    r"[,\s]+(inc|llc|ltd|limited|corp|corporation|co|gmbh|s\.?p\.?a|s\.?r\.?l|b\.?v|a\.?g|plc|sa|nv)\.?$",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )
}


def strip_diacritics(text: str) -> str:
    """'Munchen' <- 'München', so the location lists can stay ASCII."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    """Lowercase, de-accent, collapse whitespace. For display-safe comparison."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", strip_diacritics(str(text)).lower()).strip()


def slug(text: str) -> str:
    """Aggressive form used inside dedupe keys: alphanumerics only."""
    return _NON_ALNUM.sub("", normalize_text(text))


def normalize_company(name: str) -> str:
    return slug(_COMPANY_SUFFIXES.sub("", normalize_text(name)))


def normalize_title(title: str) -> str:
    """Drop emoji/markers so '🛂 Engineer' and 'Engineer' are the same role."""
    cleaned = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", " ", str(title or ""))
    return slug(cleaned)


def to_mmddyyyy(value, *, fallback: datetime | None = None) -> str:
    """Coerce a source's date into the MMDDYYYY string used in the dedupe key.

    Handles the three shapes the real sources emit:
      - unix epoch int/float  (Simplify's `date_posted`)
      - ISO-8601 string
      - bare 'Aug 05' with no year (vanshb03 README) -- year inferred

    `fallback` (the run's first-seen date) is used when a source gives no date
    at all. Without it the date slot would be empty and a repost would collide
    with the original, defeating the point of having the date in the key.
    """
    now = fallback or datetime.now(timezone.utc)

    if value is None or value == "":
        return now.strftime("%m%d%Y")

    # Epoch seconds (Simplify uses ints like 1767841111).
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            seconds = float(value)
            # Tolerate millisecond epochs.
            if seconds > 1e11:
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%m%d%Y")
        except (ValueError, OverflowError, OSError):
            return now.strftime("%m%d%Y")

    text = str(value).strip()

    # ISO-8601, with or without a time component.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.strftime("%m%d%Y")
    except ValueError:
        pass

    # 'Aug 05' / 'Aug 5' -- month + day, no year.
    match = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})$", text)
    if match:
        month = _MONTHS.get(match.group(1)[:3].lower())
        day = int(match.group(2))
        if month:
            candidate = _resolve_yearless(month, day, now)
            if candidate:
                return candidate.strftime("%m%d%Y")

    return now.strftime("%m%d%Y")


def _resolve_yearless(month: int, day: int, now: datetime) -> datetime | None:
    """Pick the year for a month/day with none given.

    README tables list newest-first and only ever contain past postings, so the
    right year is the most recent one that doesn't land in the future. A small
    forward tolerance absorbs timezone skew around New Year.
    """
    for year in (now.year, now.year - 1):
        try:
            candidate = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:  # Feb 29 in a non-leap year
            continue
        if candidate <= now + timedelta(days=2):
            return candidate
    return None


def make_id(company: str, title: str, date_mmddyyyy: str) -> str:
    """Stable dedupe key: normalized company + title + MMDDYYYY posting date.

    Deliberately excludes the URL and the source's own id. The same job is
    listed by multiple repos with different tracking URLs and different UUIDs,
    so keying on either would notify twice for one job. Including the date
    means the same role reposted months later is treated as genuinely new.
    """
    raw = f"{normalize_company(company)}|{normalize_title(title)}|{date_mmddyyyy}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
