"""Sanity checks for the dedupe key and location precedence.

Run: python tests/test_core.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from filter import (
    LocationMatcher,
    SponsorMatcher,
    compile_title_exclusions,
    filter_postings,
)
from normalize import make_id, to_mmddyyyy

ROOT = Path(__file__).resolve().parent.parent
failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}, got {actual!r}")
        failures.append(label)


# --- dedupe key -------------------------------------------------------------
same_day_a = make_id("Acme, Inc.", "Software Engineer", "08052026")
same_day_b = make_id("ACME", "software engineer", "08052026")
check("company suffix + case collapse to one id", same_day_a, same_day_b)

repost = make_id("Acme", "Software Engineer", "11052026")
check("same role reposted months later is a NEW id", repost != same_day_a, True)

emoji = make_id("Acme", "Software Engineer \U0001F6C2", "08052026")
check("sponsorship emoji in title does not change id", emoji, same_day_a)

accented = make_id("Zalando SE", "Ingénieur Logiciel", "08052026")
plain = make_id("Zalando SE", "Ingenieur Logiciel", "08052026")
check("diacritics normalize", accented, plain)

# --- date coercion ----------------------------------------------------------
check("unix epoch -> MMDDYYYY", to_mmddyyyy(1767841111), "01082026")
check("ISO string -> MMDDYYYY", to_mmddyyyy("2026-08-05"), "08052026")
check("millisecond epoch", to_mmddyyyy(1767841111000), "01082026")

now = datetime(2026, 8, 13, tzinfo=timezone.utc)
check("yearless 'Aug 05' takes current year", to_mmddyyyy("Aug 05", fallback=now), "08052026")
check("yearless 'Dec 20' rolls back a year", to_mmddyyyy("Dec 20", fallback=now), "12202025")
check("missing date falls back to first-seen", to_mmddyyyy(None, fallback=now), "08132026")
check("garbage date falls back", to_mmddyyyy("TBD", fallback=now), "08132026")

# --- location matching ------------------------------------------------------
config = yaml.safe_load((ROOT / "config" / "eu_locations.yaml").read_text(encoding="utf-8"))
M = LocationMatcher(config)

check("Milan, Italy is EU", M.is_eu("Milan, Italy"), True)
check("Remote (EU) is EU", M.is_eu("Remote (EU)"), True)
check("Berlin, Germany is EU", M.is_eu("Berlin, Germany"), True)

check("London, UK is NOT EU", M.is_eu("London, UK"), False)
check("United Kingdom is NOT EU", M.is_eu("United Kingdom"), False)

# US precedence: these are US cities that share EU city names.
for us_city in ["Dublin, CA", "Dublin, OH", "Berlin, NH", "Paris, TX", "Vienna, VA", "Naples, FL"]:
    check(f"{us_city} detected as US", M.is_us(us_city), True)

check("Zurich excluded while include_non_eu_european is false", M.is_eu("Zurich, Switzerland"), False)
check("plain 'Remote' is not assumed EU", M.is_eu("Remote"), False)
check("empty location is not EU", M.is_eu(""), False)

# --- title exclusion --------------------------------------------------------
sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
settings = sources.get("settings", {})
excl = compile_title_exclusions(settings.get("exclude_title_patterns"))


def excluded(title):
    from normalize import normalize_text
    normalized = normalize_text(title)
    return any(term.search(normalized) for term in excl)


for title in ["Software Engineer Intern", "Summer 2026 Internship",
              "Data Science Interns", "SWE Intern (Backend)",
              "Internship - Machine Learning", "intern"]:
    check(f"drops {title!r}", excluded(title), True)

for title in ["International Tax Analyst", "Internal Tools Engineer",
              "Software Engineer, New Grad", "Backend Engineer (Internationalization)"]:
    check(f"keeps {title!r}", excluded(title), False)

# End-to-end: exclusion runs before the location check, and only on titles.
sample = [
    {"id": "a", "title": "Software Engineer Intern", "location": "Berlin, Germany", "active": True},
    {"id": "b", "title": "Software Engineer", "location": "Berlin, Germany", "active": True},
    {"id": "c", "title": "Internal Tools Engineer", "location": "Milan, Italy", "active": True},
]
kept_ids = [p["id"] for p in filter_postings(sample, config, settings)]
check("filter_postings drops the intern role only", kept_ids, ["b", "c"])

# --- H-1B sponsor matching --------------------------------------------------
sponsor_cfg = yaml.safe_load((ROOT / "config" / "h1b_sponsors.yaml").read_text(encoding="utf-8"))
S = SponsorMatcher(sponsor_cfg)

check("sponsor list is non-empty", bool(S), True)

for name in ["Amazon", "Google", "Apple", "Palantir", "Citadel Securities", "Optiver"]:
    check(f"{name} is a known sponsor", S.is_sponsor(name), True)

# Prefix matching: one entry should cover a company's subsidiaries.
check("prefix: Amazon Web Services", S.is_sponsor("Amazon Web Services"), True)
check("prefix: Amazon Robotics", S.is_sponsor("Amazon Robotics"), True)
check("legal suffix tolerated", S.is_sponsor("Roblox, Inc."), True)
check("leading article stripped", S.is_sponsor("The Home Depot"), True)

# The whole point of prefix-anchoring rather than substring matching.
check("'Applied Materials' does NOT match Johns Hopkins APL",
      S.is_sponsor("Johns Hopkins Applied Physics Laboratory"), False)

# Cleared-defense primes are deliberately absent.
for name in ["Northrop Grumman", "RTX", "L3Harris Technologies", "CACI",
             "Peraton", "SpaceX", "Leidos"]:
    check(f"{name} is NOT badged as a sponsor", S.is_sponsor(name), False)

check("empty company is not a sponsor", S.is_sponsor(""), False)
check("missing sponsor config degrades safely", bool(SponsorMatcher(None)), False)

# --- sponsorship branch end-to-end ------------------------------------------
us_settings = dict(settings, allow_unknown_sponsorship=True)
sample = [
    {"id": "known", "title": "Software Engineer", "location": "Seattle, WA",
     "company": "Amazon", "sponsorship_flag": "unknown", "active": True},
    {"id": "unlisted", "title": "Software Engineer", "location": "Reston, VA",
     "company": "Peraton", "sponsorship_flag": "unknown", "active": True},
    {"id": "explicit", "title": "Software Engineer", "location": "Austin, TX",
     "company": "Some Startup", "sponsorship_flag": "yes", "active": True},
    {"id": "refused", "title": "Software Engineer", "location": "Chantilly, VA",
     "company": "Amazon", "sponsorship_flag": "no", "active": True},
]
result = {p["id"]: p for p in filter_postings(sample, config, us_settings, sponsor_cfg)}

check("known sponsor is kept", "known" in result, True)
check("known sponsor is badged", result["known"].get("known_sponsor"), True)
check("known sponsor is not marked unverified", result["known"].get("unverified"), False)
check("unlisted US company still rides along", "unlisted" in result, True)
check("unlisted US company is marked unverified", result["unlisted"]["unverified"], True)
check("explicit sponsorship wins without the list", result["explicit"]["unverified"], False)
check("explicit 'no' is dropped even for a listed sponsor", "refused" in result, False)

# Strict mode: unlisted companies dropped entirely.
strict = dict(settings, allow_unknown_sponsorship=False)
strict_ids = {p["id"] for p in filter_postings(sample, config, strict, sponsor_cfg)}
check("strict mode keeps the known sponsor", "known" in strict_ids, True)
check("strict mode drops the unlisted company", "unlisted" in strict_ids, False)
check("strict mode keeps explicit sponsorship", "explicit" in strict_ids, True)

# --- clearance title exclusions ---------------------------------------------
for title in ["Software Engineer TS/SCI Poly", "Associate Software Engineer - Ts/Sci",
              "Junior Software Developer - Active TS/SCI with Poly Required"]:
    check(f"drops cleared role {title!r}", excluded(title), True)

for title in ["Data Scientist", "Research Scientist, Computer Science",
              "Software Engineer, Polygon Rendering"]:
    check(f"clearance terms do not touch {title!r}", excluded(title), False)

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
