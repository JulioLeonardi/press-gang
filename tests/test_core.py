"""Sanity checks for the dedupe key and location precedence.

Run: python tests/test_core.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from filter import LocationMatcher
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

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
