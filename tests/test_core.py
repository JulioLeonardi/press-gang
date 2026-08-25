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

# --- non-CS role exclusions -------------------------------------------------
for title in ["Enterprise Account Executive, Financial Services",
              "Business Development Representative", "Sales Development Representative - DACH",
              "Commercial(e) Terrain", "Conseiller de vente",
              "Asturian Language Specialist - Freelance AI Trainer Project",
              "Field Service Technician 1", "Lithography Maintenance Technician",
              "Avionics Hardware Engineer", "Technicien de maintenance CVC H/F",
              "Working Student | IT (Werkstudent)", "Alternant - Data Analyst H/F",
              "Stagiaire Marketing & Reseaux Sociaux", "Praktikant:in (m/w/d) - Sales",
              "MODELE INTERMARCHE - EMPLOYE COMMERCIAL (H/F)", "Hote d'accueil (H/F)",
              "Chef d'equipe logistique (H/F)", "Junior Accountant, Accounts Payable",
              "Associate Legal Counsel", "Talent Acquisition Partner"]:
    check(f"drops non-CS role {title!r}", excluded(title), True)

# The traps. Each of these was killed by an obvious-looking bare term during
# tuning; the config uses a narrower phrase (or nothing) because of them. If a
# retune reintroduces the bare term, these fail rather than silently costing
# you CS roles.
for title in ["Quality Assurance Engineer - Development",             # not "assurance"
              "Product Security Engineer Graduate (Security Assurance)",
              "Software Engineer New Grad - Hardware Tools and Methodology",  # not "hardware"
              "Research Assistant/Programmer",                        # not "assistant"
              "Graduate Research Assistant",
              "Marketing Science Analyst",                            # not "marketing"
              "Programmer Analyst - Marketing Analytics",
              "New Grad 2026: Machine Learning Graduate (eCommerce User Growth)",
              "Forward Deployed Software Engineer New Grad - Commercial",     # not "commercial"
              "Software Development Engineer - AI/LLM Network - Global Frontier "
              "Tech Recruitment Program",                             # not "recruitment"
              "Application Support Engineer",                         # not "support"
              "AI Support Engineer - Dublin",
              # Plain CS roles, as a floor.
              "Backend Engineer", "Site Reliability Engineer", "Embedded Software Engineer",
              "Firmware Engineer", "Full Stack Developer", "Junior Software Developer",
              "Machine Learning Engineer", "DevOps Engineer"]:
    check(f"non-CS terms do not touch {title!r}", excluded(title), False)

# --- eu_parquet row transform -----------------------------------------------
# The transform only; _fetch_eu_parquet's HTTP range reads are not exercised.
from fetch import eu_rows_to_postings  # noqa: E402

eu_companies = {"gh-acme": "Acme AI", "wttj-brand": "Brand SAS"}
eu_options = {
    "role_families": ["engineering", "ml-ai"],
    "exclude_seniority": ["senior", "intern"],
    "senior_title_patterns": ["senior", "lead", "manager"],
}


def eu_row(**overrides):
    row = {
        "id": "abc123", "company_slug": "gh-acme", "title": "Software Engineer",
        "url": "https://example.com/j/1", "location": "Berlin, Germany",
        "seniority": None, "role_family": "engineering",
        "posted_at": datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def eu_titles(*rows):
    return [p["title"] for p in eu_rows_to_postings(list(rows), eu_companies, eu_options, "eu")]


one = eu_rows_to_postings([eu_row()], eu_companies, eu_options, "eu")[0]
check("slug resolves to the company display name", one["company"], "Acme AI")
check("keeps the source's own stable id", one["id"], "abc123")
check("posted_at -> MMDDYYYY", one["date_posted"], "08052026")
check("sponsorship is unknown (column is 100% null upstream)",
      one["sponsorship_flag"], "unknown")
check("live snapshot means active", one["active"], True)

check("unknown company_slug is dropped", eu_titles(eu_row(company_slug="nope")), [])
check("aggregator 'via-' stub is dropped (absent from the company map)",
      eu_titles(eu_row(company_slug="via-remoteok-x")), [])
check("blank location falls back to Unspecified",
      eu_rows_to_postings([eu_row(location="")], eu_companies, eu_options, "eu")[0]["location"],
      "Unspecified")

# role_family: tagged-and-outside is dropped, untagged rides along
check("tagged role_family outside the allowlist is dropped",
      eu_titles(eu_row(role_family="sales")), [])
check("tagged role_family inside the allowlist is kept",
      eu_titles(eu_row(role_family="ml-ai")), ["Software Engineer"])
check("UNTAGGED role_family is kept (upstream tagger lags the scraper)",
      eu_titles(eu_row(role_family=None)), ["Software Engineer"])

# seniority: an explicit tag wins; only untagged rows fall back to the title
check("tagged senior is dropped", eu_titles(eu_row(seniority="senior")), [])
check("tagged intern is dropped", eu_titles(eu_row(seniority="intern")), [])
check("tagged junior is kept", eu_titles(eu_row(seniority="junior")), ["Software Engineer"])
check("untagged senior-sounding title is dropped",
      eu_titles(eu_row(title="Senior Software Engineer")), [])
check("untagged 'Engineering Manager' is dropped",
      eu_titles(eu_row(title="Engineering Manager")), [])
check("a seniority tag overrides the title guard",
      eu_titles(eu_row(title="Senior Software Engineer", seniority="junior")),
      ["Senior Software Engineer"])
check("title guard is whole-word: 'Leader' does not match 'lead'",
      eu_titles(eu_row(title="Team Leader Onboarding")), ["Team Leader Onboarding"])
check("title guard is whole-word: 'Ambassador' does not match 'sr'",
      eu_titles(eu_row(title="Developer Ambassador")), ["Developer Ambassador"])

check("no options -> nothing is filtered out",
      [p["title"] for p in eu_rows_to_postings(
          [eu_row(role_family="sales", seniority="senior")], eu_companies, {}, "eu")],
      ["Software Engineer"])

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
