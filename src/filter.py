"""Title, location, and sponsorship filtering.

A posting passes if:
    (0) its title is not excluded by exclude_title_patterns, AND
    (A) it is EU-located,  OR
    (B) it is US-located AND sponsorship is plausible -- meaning the source
        says "Offers Sponsorship", OR the company is a known H-1B sponsor,
        OR the flag is unknown and allow_unknown_sponsorship is on.

The known-sponsor list (config/h1b_sponsors.yaml) exists because the sources'
own sponsorship field is nearly useless: ~99% of rows say "Other" (meaning
unspecified) and only ~28 active US postings say "Offers Sponsorship". See
that file's header for the full rationale.
"""

from __future__ import annotations

import logging
import re

from normalize import normalize_company, normalize_text

log = logging.getLogger(__name__)

# Trailing ", TX" style state codes — the shape both sources use for US roles.
_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC PR"
).split()
_US_STATE_RE = re.compile(r",\s*(" + "|".join(_US_STATES) + r")\b", re.IGNORECASE)
_US_COUNTRY_RE = re.compile(r"\b(united states|u\.?s\.?a?\.?)\b", re.IGNORECASE)


def compile_title_exclusions(patterns) -> list[re.Pattern]:
    """Word-boundary matchers for unwanted role titles.

    Boundaries matter here: a bare substring test on "intern" also swallows
    "International Tax Analyst" and "Internal Tools Engineer". Each term is
    matched whole, so "internship" needs its own entry alongside "intern".
    """
    return [
        re.compile(r"\b" + re.escape(normalize_text(p)) + r"\b")
        for p in (patterns or [])
        if p
    ]


class SponsorMatcher:
    """Membership test against the curated H-1B sponsor list.

    Matching is exact-or-prefix on the normalized company name, so one entry
    ("Amazon") covers "Amazon Web Services" and "Amazon Robotics". A contains
    match was tried first and rejected: it let "Applied Materials" match
    "Johns Hopkins Applied Physics Laboratory", which is the exact class of
    citizenship-required employer the list is meant to keep out.
    """

    def __init__(self, sponsor_config: dict | None):
        groups = (sponsor_config or {}).get("sponsors") or {}
        # Accept either a flat list or the grouped dict the config ships with,
        # so deleting a group never changes the file's shape.
        if isinstance(groups, dict):
            names = [n for group in groups.values() for n in (group or [])]
        else:
            names = list(groups)

        self.names = {self._key(n) for n in names if n}
        self.names.discard("")
        # Longest first so the most specific entry wins when reporting a hit.
        self._ordered = sorted(self.names, key=len, reverse=True)

    @staticmethod
    def _key(name: str) -> str:
        """Normalized form, with a leading article dropped.

        Sources are inconsistent about it -- "The Home Depot" and "The Boeing
        Company" appear with the article, most names without. Stripping it on
        both sides means the list doesn't need duplicate entries.
        """
        normalized = normalize_company(name)
        if normalized.startswith("the") and len(normalized) > 5:
            return normalized[3:]
        return normalized

    def __bool__(self) -> bool:
        return bool(self.names)

    def is_sponsor(self, company: str) -> bool:
        normalized = self._key(company)
        if not normalized:
            return False
        if normalized in self.names:
            return True
        return any(normalized.startswith(name) for name in self._ordered)


class LocationMatcher:
    def __init__(self, location_config: dict):
        self.exclude = [normalize_text(p) for p in location_config.get("exclude_patterns", [])]
        self.remote = [normalize_text(p) for p in location_config.get("remote_patterns", [])]

        terms = list(location_config.get("countries", [])) + list(location_config.get("cities", []))
        if location_config.get("include_non_eu_european"):
            terms += list(location_config.get("non_eu_european", []))
        # Word-boundary match so "Cork" doesn't fire inside "Corktown".
        self.eu_terms = [
            re.compile(r"\b" + re.escape(normalize_text(t)) + r"\b") for t in terms if t
        ]

    def is_us(self, location: str) -> bool:
        return bool(_US_STATE_RE.search(location) or _US_COUNTRY_RE.search(location))

    def is_eu(self, location: str) -> bool:
        """EU check. US precedence is handled by the caller, not here."""
        normalized = normalize_text(location)
        if not normalized:
            return False
        if any(pattern in normalized for pattern in self.exclude):
            return False
        if any(pattern in normalized for pattern in self.remote):
            return True
        return any(term.search(normalized) for term in self.eu_terms)


def filter_postings(
    postings: list[dict],
    location_config: dict,
    settings: dict,
    sponsor_config: dict | None = None,
) -> list[dict]:
    matcher = LocationMatcher(location_config)
    sponsors = SponsorMatcher(sponsor_config)
    require_active = settings.get("require_active", True)
    allow_unknown = settings.get("allow_unknown_sponsorship", False)
    title_exclusions = compile_title_exclusions(settings.get("exclude_title_patterns"))

    kept, stats = [], {
        "inactive": 0, "title": 0, "eu": 0,
        "us_sponsored": 0, "us_known_sponsor": 0, "us_unverified": 0, "rejected": 0,
    }

    for posting in postings:
        if require_active and not posting.get("active", True):
            stats["inactive"] += 1
            continue

        title = normalize_text(posting.get("title", ""))
        if any(term.search(title) for term in title_exclusions):
            stats["title"] += 1
            continue

        # Multi-location postings: any one qualifying location is enough.
        parts = [p.strip() for p in str(posting.get("location", "")).split("|") if p.strip()]
        if not parts:
            parts = [str(posting.get("location", ""))]

        matched = False
        for part in parts:
            # US takes precedence: many EU city names are also US cities
            # (Dublin CA, Berlin NH, Paris TX), so check US membership first.
            if matcher.is_us(part):
                flag = posting.get("sponsorship_flag", "unknown")
                if flag == "no":
                    break  # explicit "no sponsorship" / citizenship required
                if flag == "yes":
                    posting["match_reason"] = "US + sponsorship"
                    posting["unverified"] = False
                    stats["us_sponsored"] += 1
                    matched = True
                    break
                # flag == "unknown": the source told us nothing, so fall back
                # to the company-level signal. A known sponsor is treated as
                # verified; anything else only rides along if allow_unknown is
                # on, and is flagged so the Discord embed can say so.
                if sponsors.is_sponsor(posting.get("company", "")):
                    posting["match_reason"] = "US + known H-1B sponsor"
                    posting["unverified"] = False
                    posting["known_sponsor"] = True
                    stats["us_known_sponsor"] += 1
                    matched = True
                    break
                if allow_unknown:
                    posting["match_reason"] = "US, sponsorship unverified"
                    posting["unverified"] = True
                    stats["us_unverified"] += 1
                    matched = True
                    break
            elif matcher.is_eu(part):
                posting["match_reason"] = "EU location"
                posting["unverified"] = False
                stats["eu"] += 1
                matched = True
                break

        if matched:
            kept.append(posting)
        else:
            stats["rejected"] += 1

    log.info(
        "filter: kept %d (eu=%d, us_sponsored=%d, us_known_sponsor=%d, us_unverified=%d), "
        "dropped %d (inactive=%d, title=%d, no_match=%d)",
        len(kept), stats["eu"], stats["us_sponsored"],
        stats["us_known_sponsor"], stats["us_unverified"],
        stats["inactive"] + stats["title"] + stats["rejected"],
        stats["inactive"], stats["title"], stats["rejected"],
    )
    if not sponsors:
        log.warning("h1b sponsor list is empty -- every US role falls back to unverified")
    return kept
