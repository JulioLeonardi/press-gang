"""Location + sponsorship filtering.

A posting passes if:
    (A) it is EU-located,  OR
    (B) it is US-located AND sponsorship is offered
        (or unknown, if allow_unknown_sponsorship is on).
"""

from __future__ import annotations

import logging
import re

from normalize import normalize_text

log = logging.getLogger(__name__)

# Trailing ", TX" style state codes — the shape both sources use for US roles.
_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC PR"
).split()
_US_STATE_RE = re.compile(r",\s*(" + "|".join(_US_STATES) + r")\b", re.IGNORECASE)
_US_COUNTRY_RE = re.compile(r"\b(united states|u\.?s\.?a?\.?)\b", re.IGNORECASE)


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


def filter_postings(postings: list[dict], location_config: dict, settings: dict) -> list[dict]:
    matcher = LocationMatcher(location_config)
    require_active = settings.get("require_active", True)
    allow_unknown = settings.get("allow_unknown_sponsorship", False)

    kept, stats = [], {"inactive": 0, "eu": 0, "us_sponsored": 0, "rejected": 0}

    for posting in postings:
        if require_active and not posting.get("active", True):
            stats["inactive"] += 1
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
                if flag == "yes" or (flag == "unknown" and allow_unknown):
                    posting["match_reason"] = "US + sponsorship"
                    posting["unverified"] = flag == "unknown"
                    stats["us_sponsored"] += 1
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
        "filter: kept %d (eu=%d, us_sponsored=%d), dropped %d (inactive=%d, no_match=%d)",
        len(kept), stats["eu"], stats["us_sponsored"],
        stats["inactive"] + stats["rejected"], stats["inactive"], stats["rejected"],
    )
    return kept
