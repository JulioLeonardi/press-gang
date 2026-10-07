"""Server: token, CORS, caching, PDF serving, and reload on bank change.

Run: python tests/test_server.py
Rendering is stubbed except in the last block, which needs resume/bank.yaml
and pdflatex and skips without them.
"""

import copy
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server.app as server  # noqa: E402
from tailor.bank import BANK_PATH, load_yaml  # noqa: E402
from tailor.select import select  # noqa: E402

TOKEN = "test-token"
AUTH = {"X-Tailor-Token": TOKEN}
JD = "Requirements:\n- Go and Kubernetes\n- PostgreSQL"
failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected!r}, got {actual!r}")
        failures.append(label)


renders = []


def fake_tailor(bank, aliases, keywords, out_dir):
    renders.append(out_dir.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / "Test_Person_Resume.pdf"
    pdf.write_bytes(b"%PDF-1.5 fake")
    return select(bank, keywords, aliases), pdf


def write_bank(path, bank):
    path.write_text(yaml.safe_dump(bank, sort_keys=False), encoding="utf-8")
    stat = path.stat()   # bump mtime past the filesystem's resolution
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9 * (len(renders) + 1)))


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    bank = load_yaml(ROOT / "tests" / "fixtures" / "bank" / "select.yaml")
    bank["header"] = {"name": "Test Person"}
    bank_path = tmp / "bank.yaml"
    write_bank(bank_path, bank)
    server.cli.tailor, real_tailor = fake_tailor, server.cli.tailor
    client = TestClient(server.create_app(TOKEN, "abcdefghijklmnop", bank_path, tmp / "out"))

    # --- token ----------------------------------------------------------------
    check("no token: /health is refused", client.get("/health").status_code, 401)
    check("no token: /tailor is refused", client.post("/tailor", json={"jd_text": JD}).status_code, 401)
    check("no token: a resume PDF is refused", client.get("/resume/0123456789abcdef.pdf").status_code, 401)
    check("wrong token is refused",
          client.get("/health", headers={"X-Tailor-Token": "nope"}).status_code, 401)
    try:
        server.create_app("")
        check("create_app('') raises", False, True)
    except ValueError:
        check("create_app('') raises", True, True)

    # --- CORS -----------------------------------------------------------------
    def preflight(origin):
        r = client.options("/tailor", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                               "Access-Control-Request-Headers": "X-Tailor-Token"})
        return r.headers.get("access-control-allow-origin")

    check("CORS allows the extension's origin",
          preflight("chrome-extension://abcdefghijklmnop"), "chrome-extension://abcdefghijklmnop")
    check("CORS gives other origins nothing", preflight("https://example.com"), None)

    # --- health and tailoring ----------------------------------------------------
    health = client.get("/health", headers=AUTH).json()
    check("health: fixture bank is valid", (health["valid"], health["errors"]), (True, []))

    r = client.post("/tailor", headers=AUTH, json={"jd_text": JD, "company": "Acme"})
    body = r.json()
    check("tailor: 200", r.status_code, 200)
    check("tailor: covers the JD from the bank", sorted(body["covered"]), ["Go", "Kubernetes", "PostgreSQL"])
    check("tailor: keywords carry source and evidence",
          sorted(body["keywords"][0]), ["evidence", "source", "term", "weight"])
    check("tailor: not cached the first time", body["cached"], False)

    again = client.post("/tailor", headers=AUTH,
                        json={"jd_text": "  Requirements:\n- Go  and Kubernetes\n-  PostgreSQL ", "company": "acme"})
    check("tailor: whitespace and company case hit the cache",
          (again.json()["cached"], again.json()["id"], len(renders)), (True, body["id"], 1))

    pdf = client.get(body["pdf_url"], headers=AUTH)
    check("resume: served as a PDF", (pdf.status_code, pdf.headers["content-type"]), (200, "application/pdf"))
    check("resume: download name has no company in it",
          'filename="Test_Person_Resume.pdf"' in pdf.headers["content-disposition"], True)
    check("resume: unknown id is 404", client.get("/resume/0123456789abcdef.pdf", headers=AUTH).status_code, 404)
    check("resume: a path-like id is 404", client.get("/resume/..%2F..%2Fx.pdf", headers=AUTH).status_code, 404)
    check("tailor: empty JD is rejected", client.post("/tailor", headers=AUTH, json={"jd_text": ""}).status_code, 422)

    # --- reload -------------------------------------------------------------------
    broken = copy.deepcopy(bank)
    del broken["sections"][0]["bullets"][0]["variants"][0]["voice"]
    write_bank(bank_path, broken)
    health = client.get("/health", headers=AUTH).json()
    check("reload: a broken bank shows as invalid", (health["valid"], len(health["errors"]) > 0), (False, True))
    r = client.post("/tailor", headers=AUTH, json={"jd_text": JD, "company": "Acme"})
    check("reload: /tailor refuses a broken bank, even for a cached JD", r.status_code, 503)

    write_bank(bank_path, bank)
    r = client.post("/tailor", headers=AUTH, json={"jd_text": JD, "company": "Acme"})
    check("reload: a fixed bank serves again, from cache (same content)",
          (r.status_code, r.json()["cached"]), (200, True))

    edited = copy.deepcopy(bank)
    edited["sections"][0]["title"] = "Platform Intern"
    write_bank(bank_path, edited)
    r = client.post("/tailor", headers=AUTH, json={"jd_text": JD, "company": "Acme"})
    check("reload: an edited bank re-renders instead of serving the old PDF", r.json()["cached"], False)

    server.cli.tailor = real_tailor

# --- real bank, real render -------------------------------------------------------------
if BANK_PATH.exists() and shutil.which("pdflatex"):
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(server.create_app(TOKEN, out_dir=Path(tmp)))
        jd = (ROOT / "tests" / "jds" / "stripe_new_grad.txt").read_text(encoding="utf-8")
        r = client.post("/tailor", headers=AUTH, json={"jd_text": jd, "company": "Stripe"})
        check("real bank: /tailor renders", r.status_code, 200)
        pdf = client.get(r.json()["pdf_url"], headers=AUTH)
        check("real bank: the PDF downloads", (pdf.status_code, pdf.content[:4]), (200, b"%PDF"))
else:
    print("SKIP  real-bank check needs resume/bank.yaml and pdflatex")

print()
if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("all checks passed")
