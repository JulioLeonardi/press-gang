"""Local tailoring API for the browser extension. Binds to 127.0.0.1 only.

Every route needs the X-Tailor-Token header, so a web page in the browser
can't call it. The bank, aliases and taxonomy reload when their files change;
a bank that fails the lint makes /tailor refuse rather than use stale data.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import tailor.__main__ as cli
from tailor.bank import ALIASES_PATH, BANK_PATH, load_yaml
from tailor.extract import TAXONOMY_PATH, extract, vocabulary
from tailor.render import OUT_DIR, PageOverflow
from tailor.select import NoLayout
from tailor.validate import validate

_PDF_ID = re.compile(r"[0-9a-f]{16}")


class TailorRequest(BaseModel):
    jd_text: str = Field(min_length=1, max_length=200_000)
    url: str | None = None
    company: str | None = None
    title: str | None = None


class Bank:
    """The bank and its lookup files, reloaded and re-linted when any changes."""

    def __init__(self, bank_path: Path):
        self.paths = [bank_path, ALIASES_PATH, TAXONOMY_PATH]
        self.stamp = None
        self.lock = threading.Lock()

    def current(self) -> "Bank":
        stamp = tuple(p.stat().st_mtime_ns if p.exists() else None for p in self.paths)
        with self.lock:
            if stamp != self.stamp:
                self._load()
                self.stamp = stamp
        return self

    def _load(self) -> None:
        bank_path, aliases_path, taxonomy_path = self.paths
        raw = b"".join(p.read_bytes() if p.exists() else b"" for p in self.paths)
        self.version = hashlib.sha256(raw).hexdigest()[:12]
        if not bank_path.exists():
            self.data, self.errors, self.warnings = {}, [f"{bank_path} not found"], []
            return
        self.data = load_yaml(bank_path)
        self.aliases, self.taxonomy = load_yaml(aliases_path), load_yaml(taxonomy_path)
        self.errors, self.warnings = validate(self.data, self.aliases)
        self.vocab = vocabulary(self.data, self.aliases, self.taxonomy)


def _cache_key(version: str, req: TailorRequest) -> str:
    jd = " ".join(req.jd_text.split())
    company = (req.company or "").strip().lower()
    return hashlib.sha256(f"{version}\0{company}\0{jd}".encode()).hexdigest()[:16]


def create_app(token: str, extension_id: str | None = None,
               bank_path: Path = BANK_PATH, out_dir: Path = OUT_DIR) -> FastAPI:
    if not token:
        raise ValueError("a token is required")

    def require_token(x_tailor_token: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_tailor_token.encode(), token.encode()):
            raise HTTPException(401, "missing or wrong X-Tailor-Token")

    app = FastAPI(title="tailor", dependencies=[Depends(require_token)])
    if extension_id:
        app.add_middleware(CORSMiddleware, allow_origins=[f"chrome-extension://{extension_id}"],
                           allow_methods=["GET", "POST"], allow_headers=["X-Tailor-Token", "Content-Type"])
    bank = Bank(bank_path)
    cache: dict[str, dict] = {}
    render_lock = threading.Lock()   # one pdflatex run at a time

    @app.get("/health")
    def health() -> dict:
        b = bank.current()
        return {"bank_version": b.version, "valid": not b.errors,
                "errors": b.errors, "warnings": len(b.warnings)}

    @app.post("/tailor")
    def tailor_jd(req: TailorRequest) -> dict:
        b = bank.current()
        if b.errors:
            raise HTTPException(503, {"message": "bank fails the lint; run python -m tailor.validate",
                                      "errors": b.errors})
        key = _cache_key(b.version, req)
        if key in cache:
            return {**cache[key], "cached": True}
        keywords = extract(req.jd_text, b.vocab, b.aliases, b.taxonomy, req.company)
        try:
            with render_lock:
                selection, _ = cli.tailor(b.data, b.aliases, keywords, out_dir / key)
        except (NoLayout, PageOverflow) as e:
            raise HTTPException(422, str(e))
        cache[key] = {
            "id": key,
            "bank_version": b.version,
            "score": selection.score,
            "voice": selection.voice,
            "keywords": [asdict(k) for k in keywords],
            "covered": selection.covered,
            "uncovered": [asdict(k) for k in selection.uncovered],
            "layout": selection.layout,
            "pdf_url": f"/resume/{key}.pdf",
        }
        return {**cache[key], "cached": False}

    @app.get("/resume/{pdf_id}.pdf")
    def resume_pdf(pdf_id: str) -> FileResponse:
        if not _PDF_ID.fullmatch(pdf_id):
            raise HTTPException(404, "no such resume")
        name = bank.current().data.get("header", {}).get("name", "Resume").replace(" ", "_") + "_Resume.pdf"
        path = out_dir / pdf_id / name
        if not path.is_file():
            raise HTTPException(404, "no such resume")
        return FileResponse(path, media_type="application/pdf", filename=name)

    return app
