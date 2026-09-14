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
check("yearless 'Aug 05' takes current year", to_mmddyyyy("Aug 05", now=now), "08052026")
check("yearless 'Dec 20' rolls back a year", to_mmddyyyy("Dec 20", now=now), "12202025")

# The skew window is one day, not two. A yearless date further ahead than that
# is last year's -- the regression that put fourteen "Aug 28" rows (year-old
# postings, some still titled "New Grad 2025") at the top of the 2026-08-26
# board as future-dated entries.
check("yearless date one day ahead is still this year (clock skew)",
      to_mmddyyyy("Aug 14", now=now), "08142026")
check("yearless date two days ahead rolls back a year",
      to_mmddyyyy("Aug 15", now=now), "08152025")

# No date means no date. Substituting the run time used to pile every dateless
# posting into a fake "today" group that moved forward on every rebuild.
check("missing date is empty, not today", to_mmddyyyy(None, now=now), "")
check("empty-string date is empty", to_mmddyyyy("", now=now), "")
check("garbage date is empty", to_mmddyyyy("TBD", now=now), "")
check("an undated posting still gets a stable id",
      make_id("Acme", "Software Engineer", ""),
      make_id("Acme", "Software Engineer", ""))

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

# Added with the company ATS boards: the noise they let through.
for title in ["Account Development Representative, DACH & CEE", "Junior Recipe Developer (all genders)",
              "Product Financial Controller, Balance Platform", "Fleet Development Analyst",
              "Fleet Development Success - Key Accounts Specialist", "Customer Support Agent- IT",
              "Merchant Security Expert with German and English", "Marketing Analyst - Growth & Analytics (M/F/X)",
              "Restaurant Onboarding Specialist - Data Validation", "Commerce Platform Account Specialist - Thessaloniki",
              "Retail Development Associate", "Procurement Systems Analyst",
              "Associate (AI) Solution Consultant (DACH) - Orbit Program", "Site QA/QC Specialist (f/m/x)",
              "New Grad 2026: Associate Product Designer", "HQ -  AI Business Analyst"]:
    check(f"drops non-CS role {title!r}", excluded(title), True)

# And their traps: the bare word each phrase above was narrowed from.
for title in ["Python AI Agent Engineer (Mid-level)",                        # not "agent"
              "Software Engineer - Kubernetes Specialist", "Devops Specialist F/H",  # not "specialist"
              "Consultant Cybersecurite OT (H/F)",                          # not "consultant"
              "New Grad 2026: Software Engineer (Commerce Ads)",             # not "commerce"
              "Quantitative Strategy Developer New Grad",                   # not "strategy"
              "ASIC Design Engineer - Cache Controller",                    # not "controller"
              "Kotlin Backend Engineer - Retail Group",                     # not "retail"
              "Kotlin/Java Developer (Merchant Response)",                  # not "merchant"
              "New Grad 2026: Backend Software Engineer (Customer Service Platform)",  # not "customer service"
              "Security Analyst (compliance and controls)",                 # not "compliance"
              "Software Engineer, Stripe Tax", "Finance Data Engineer",
              "Fullstack Software Engineer (x/f/m) - Payroll & Benefits Platform for HR",
              "IT Operations Engineer, Intelligent Platforms Alliance",
              "Account Solution Engineer", "Customer Success Engineer",
              "Analytics Engineer I - AI & Data Enablement"]:
    check(f"new exclusions do not touch {title!r}", excluded(title), False)

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
check("null posted_at is undated, not stamped with the run date",
      eu_rows_to_postings([eu_row(posted_at=None)], eu_companies, eu_options, "eu")[0]["date_posted"],
      "")
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

# require_title_patterns: the positive screen. 68% of this source's rows carry
# no role_family tag, so the allowlist above never applies to them and a
# blocklist alone cannot keep a general EU job board down to CS roles.
cs_options = dict(eu_options, require_title_patterns=[
    "software", "engineer", "data", "ai", "it", "ingenieur", "entwickler",
    "c++", ".net", "system administrator", "cobol",
])


def cs_titles(*rows):
    return [p["title"] for p in eu_rows_to_postings(list(rows), eu_companies, cs_options, "eu")]


check("a CS title passes the positive screen",
      cs_titles(eu_row(title="Backend Software Engineer")), ["Backend Software Engineer"])
check("a non-CS title is dropped even though nothing blocklists it",
      cs_titles(eu_row(title="Medical Science Liaison - Oncology")), [])
check("the French agency roles that prompted this are dropped",
      cs_titles(eu_row(title="Electromecanicien de maintenance - Journee"),
                eu_row(title="Gestionnaire IJSS (H/F)"),
                eu_row(title="Chef de chantier gros oeuvre VILLAS DE LUXE H/F")), [])
check("a tagged-engineering electrician is dropped too (the tag is not a "
      "software signal)",
      cs_titles(eu_row(title="Elektriker / Elektroniker Photovoltaik (m/w/d)",
                       role_family="engineering")), [])
check("non-English CS titles pass",
      cs_titles(eu_row(title="Ingenieur Logiciel"), eu_row(title="Java Entwickler (m/w/d)")),
      ["Ingenieur Logiciel", "Java Entwickler (m/w/d)"])

# The reason the matcher hand-rolls its boundaries instead of using \b: \b after
# "+" demands a word character, so r"\bc\+\+\b" can never match and the term
# would silently do nothing.
check("stack names with trailing punctuation still match ('c++')",
      cs_titles(eu_row(title="C++ Developer (m/w/d)")), ["C++ Developer (m/w/d)"])
check("stack names with leading punctuation still match ('.net')",
      cs_titles(eu_row(title="Consultant .NET")), ["Consultant .NET"])
check("multi-word terms match as phrases",
      cs_titles(eu_row(title="System Administrator (w/m/d)")), ["System Administrator (w/m/d)"])
check("the screen is whole-word: 'ai' does not fire inside 'email'",
      cs_titles(eu_row(title="Email Campaign Coordinator")), [])
check("the screen is whole-word: 'it' does not fire inside 'Recruitment'",
      cs_titles(eu_row(title="Recruitment Coordinator")), [])
check("an absent require_title_patterns screens nothing (back-compat)",
      eu_titles(eu_row(title="Medical Science Liaison - Oncology")),
      ["Medical Science Liaison - Oncology"])

# --- ats_key --------------------------------------------------------------
from normalize import ats_key  # noqa: E402

check("Greenhouse job-boards URL",
      ats_key("https://job-boards.greenhouse.io/adyen/jobs/7938074"), "gh:7938074")
check("Greenhouse legacy boards URL is the same key",
      ats_key("https://boards.greenhouse.io/adyen/jobs/7938074?t=abc"), "gh:7938074")
# How Aramente links N26: a careers-site URL with the Greenhouse id as a param.
check("careers-site URL with gh_jid matches the Greenhouse key",
      ats_key("https://n26.com/en-eu/careers/positions/8184721?gh_jid=8184721"), "gh:8184721")
check("Ashby UUID is lowercased",
      ats_key("https://jobs.ashbyhq.com/mollie/D056F390-1D70-408E-8F85-2360D41EAA84"),
      "ashby:d056f390-1d70-408e-8f85-2360d41eaa84")
check("Ashby application URL keys to the same job",
      ats_key("https://jobs.ashbyhq.com/mollie/d056f390-1d70-408e-8f85-2360d41eaa84/application"),
      "ashby:d056f390-1d70-408e-8f85-2360d41eaa84")
check("Lever",
      ats_key("https://jobs.lever.co/doctrine/0a426b04-3c62-424c-9e5c-8622780f62de"),
      "lever:0a426b04-3c62-424c-9e5c-8622780f62de")
check("SmartRecruiters API URL",
      ats_key("https://api.smartrecruiters.com/v1/companies/noota/postings/744000126256349"),
      "sr:744000126256349")
check("SmartRecruiters public URL is the same key",
      ats_key("https://jobs.smartrecruiters.com/Noota/744000126256349-backend-engineer"),
      "sr:744000126256349")
check("Recruitee keeps the company (slugs are per-company)",
      ats_key("https://amiparis.recruitee.com/o/cdi-responsable-logistique-hf"),
      "recruitee:amiparis/cdi-responsable-logistique-hf")
check("Personio .de and .com agree",
      ats_key("https://audeering.jobs.personio.de/job/1936168"),
      ats_key("https://audeering.jobs.personio.com/job/1936168"))
check("a URL with no ATS id has no key",
      ats_key("https://amazon.jobs/en/jobs/2890123/software-development-engineer"), None)
check("an empty URL has no key", ats_key(""), None)

# --- dedupe + per-group seeding ----------------------------------------------
from main import mark_seeded, split_new  # noqa: E402


def posting(pid, key=None, group="simplify", title="Software Engineer"):
    return {"id": pid, "ats_key": key, "seed_group": group, "company": "Acme", "title": title}


def ids(postings):
    return [p["id"] for p in postings]


seeded_state = {"postings": {}, "seeded_groups": ["simplify", "aramente"]}

new, unseeded = split_new([posting("a", "gh:1"), posting("b", "gh:1", group="aramente")], seeded_state)
check("one job from two sources (same ats_key, different ids) notifies once", ids(new), ["a"])

new, _ = split_new([posting("a", "gh:1"), posting("b", "gh:2")], seeded_state)
check("same title, different ATS ids are two reqs, both notify", ids(new), ["a", "b"])

# The regression a company+title fallback would cause: amazon.jobs URLs carry
# no ATS id, and Amazon posts many reqs under one title.
new, _ = split_new([posting("a"), posting("b")], seeded_state)
check("keyless postings with different ids both notify", ids(new), ["a", "b"])

state_with_key = {"postings": {"old": {"first_seen": "2026-09-01T00:00:00+00:00", "ats_key": "gh:9"}},
                  "seeded_groups": ["simplify"]}
new, _ = split_new([posting("new-id", "gh:9")], state_with_key)
check("a job already notified under another id is seen via its ats_key", ids(new), [])

new, unseeded = split_new([posting("a", group="simplify"), posting("c", group="ats/greenhouse/adyen")],
                          seeded_state)
check("a seeded group's match notifies", ids(new), ["a"])
check("an unseeded group's match is held for silent seeding", ids(unseeded), ["c"])

seed_state = {"postings": {}, "seeded_groups": ["simplify"]}
changed = mark_seeded(seed_state, unseeded, {"simplify", "ats/greenhouse/adyen"}, "2026-09-14T00:00:00+00:00")
check("seeding reports a state change", changed, True)
check("seeded postings are flagged for heartbeat", seed_state["postings"]["c"].get("seeded"), True)
check("the new group is now seeded", seed_state["seeded_groups"], ["ats/greenhouse/adyen", "simplify"])
check("re-marking already-seeded groups is not a change",
      mark_seeded(seed_state, [], {"simplify"}, "2026-09-14T00:00:00+00:00"), False)
new, unseeded = split_new([posting("d", group="ats/greenhouse/adyen")], seed_state)
check("after seeding, the group's next posting notifies", (ids(new), ids(unseeded)), (["d"], []))

# --- ATS adapters (trimmed real responses in tests/fixtures/) -----------------
import json  # noqa: E402

from ats import _PARSERS  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def parse_fixture(ats, file, company, board):
    data = json.loads((FIXTURES / file).read_text(encoding="utf-8"))
    return _PARSERS[ats](data, company, board)


gh_jobs = parse_fixture("greenhouse", "greenhouse_feedzai.json", "Feedzai", "feedzai")
check("greenhouse: every job parsed", len(gh_jobs), 3)
check("greenhouse: key is the Greenhouse job id", gh_jobs[0]["ats_key"], "gh:7882365")
check("greenhouse: key agrees with ats_key() on the job's own URL",
      gh_jobs[0]["ats_key"], ats_key(gh_jobs[0]["url"]))
check("greenhouse: seed group is per board", gh_jobs[0]["seed_group"], "ats/greenhouse/feedzai")
check("greenhouse: source_repo names the ATS for the board page", gh_jobs[0]["source_repo"], "ats/greenhouse")
check("greenhouse: company comes from companies.yaml", gh_jobs[0]["company"], "Feedzai")
check("greenhouse: location name", gh_jobs[1]["location"], "Berlin, Berlin, Germany")
# first_published 2026-05-05, updated_at 2026-08-04: an edit must not re-date the role.
check("greenhouse: dated by first_published, not updated_at", gh_jobs[1]["date_posted"], "05052026")

ab_jobs = parse_fixture("ashby", "ashby_mollie.json", "Mollie", "mollie")
check("ashby: isListed false is dropped", [p["title"] for p in ab_jobs],
      ["Application Engineer II", "Business Support Specialist - Dutch",
       "Working student - Customer Success DACH (m/f/d)"])
check("ashby: key is the job UUID", ab_jobs[0]["ats_key"], "ashby:3bcb16aa-833c-4aeb-a277-ab7603a176f9")
check("ashby: key agrees with ats_key() on jobUrl", ab_jobs[0]["ats_key"], ats_key(ab_jobs[0]["url"]))
check("ashby: publishedAt -> MMDDYYYY", ab_jobs[0]["date_posted"], "08042025")

lv_jobs = parse_fixture("lever", "lever_pigment.json", "Pigment", "pigment")
check("lever: every job parsed", len(lv_jobs), 3)
check("lever: title comes from `text`", lv_jobs[0]["title"], "Data Engineer (Growth Team)")
check("lever: key agrees with ats_key() on hostedUrl", lv_jobs[0]["ats_key"], ats_key(lv_jobs[0]["url"]))
check("lever: millisecond createdAt -> MMDDYYYY", lv_jobs[0]["date_posted"], "02192026")

ab_multi = _PARSERS["ashby"]({"jobs": [dict(json.loads((FIXTURES / "ashby_mollie.json").read_text(encoding="utf-8"))["jobs"][0],
                                            secondaryLocations=[{"location": "Amsterdam"}])]}, "Mollie", "mollie")
check("ashby: secondary locations are joined with ' | '", ab_multi[0]["location"], "Lisbon | Amsterdam")
gh_semicolon = _PARSERS["greenhouse"]({"jobs": [dict(json.loads((FIXTURES / "greenhouse_feedzai.json").read_text(encoding="utf-8"))["jobs"][0],
                                                     location={"name": "United States (Remote) ; Spain (Remote)"})]}, "Feedzai", "feedzai")
check("';'-joined locations are split so the EU part can match past a US part",
      gh_semicolon[0]["location"], "United States (Remote) | Spain (Remote)")
check("... and filter_postings then keeps it on the Spain part",
      [p["id"] for p in filter_postings(gh_semicolon, config, settings)], [gh_semicolon[0]["id"]])

# Company boards are EU-only. Amazon + unknown flag would pass as a known
# sponsor from any other source.
check("ATS postings are marked eu_only", gh_jobs[0].get("eu_only"), True)
board_us = dict(gh_jobs[0], id="board-us", company="Amazon", location="Seattle, WA")
board_multi = dict(gh_jobs[0], id="board-multi", company="Amazon", location="Seattle, WA | Berlin, Germany")
repo_us = {k: v for k, v in board_us.items() if k != "eu_only"} | {"id": "repo-us"}
check("eu_only: a US-only role is dropped, even at a known sponsor; the same role from a repo is kept",
      [p["id"] for p in filter_postings([board_us, board_multi, repo_us], config, us_settings, sponsor_cfg)],
      ["board-multi", "repo-us"])
check("eu_only: a US part is skipped, not fatal -- the Berlin part still matches as EU",
      [p["match_reason"] for p in filter_postings([dict(board_multi)], config, us_settings, sponsor_cfg)],
      ["EU location"])

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
