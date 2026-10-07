"""Run the tailoring server on 127.0.0.1.

Run: python -m server [port]          (default port 8765)

Reads TAILOR_TOKEN (required) and TAILOR_EXTENSION_ID (optional, pins CORS to
the extension) from the environment or from .env in the repo root.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from server.app import create_app
from tailor.bank import ROOT

DEFAULT_PORT = 8765


def read_env() -> dict[str, str]:
    """KEY=value lines from .env, overridden by the real environment."""
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    values.update({k: v for k, v in os.environ.items() if k.startswith("TAILOR_")})
    return values


def main(argv: list[str]) -> int:
    env = read_env()
    if not env.get("TAILOR_TOKEN"):
        print("TAILOR_TOKEN is not set. Create one and put it in .env (gitignored):\n"
              '  python -c "import secrets; print(\'TAILOR_TOKEN=\' + secrets.token_urlsafe(32))" >> .env')
        return 1
    port = int(argv[0]) if argv else DEFAULT_PORT
    app = create_app(env["TAILOR_TOKEN"], env.get("TAILOR_EXTENSION_ID") or None)
    # No websockets here; "none" also skips importing an incompatible installed copy.
    uvicorn.run(app, host="127.0.0.1", port=port, ws="none")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
