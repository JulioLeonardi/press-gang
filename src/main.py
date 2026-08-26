"""Pipeline entrypoint: fetch -> filter -> dedupe -> notify -> persist state.

First run seeds state silently (no Discord messages) so you don't get
thousands of historical postings dumped into the channel. Every run after
that notifies only on genuinely new matches.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from fetch import fetch_source  # noqa: E402
from filter import filter_postings  # noqa: E402
from notify_discord import notify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "config" / "sources.yaml"
LOCATIONS_PATH = ROOT / "config" / "eu_locations.yaml"
SPONSORS_PATH = ROOT / "config" / "h1b_sponsors.yaml"
STATE_PATH = ROOT / "state" / "seen.json"

log = logging.getLogger("job-alert-bot")


def load_state() -> dict:
    """State shape: {"version", "bootstrapped", "postings": {id: {first_seen}}}.

    `first_seen` is stored per id (not a bare id list) because pruning needs a
    timestamp, and because the board uses it to decide which rows are NEW.
    It is NOT a stand-in posting date: a source that publishes no date leaves
    `date_posted` empty and the posting renders as "Undated". See to_mmddyyyy.
    """
    if not STATE_PATH.exists():
        return {"version": 1, "bootstrapped": False, "postings": {}}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("state file unreadable (%s) -- refusing to run rather than re-notify everything", exc)
        raise SystemExit(1)

    # Tolerate a hand-written legacy list of bare ids.
    if isinstance(state, list):
        now = datetime.now(timezone.utc).isoformat()
        return {
            "version": 1,
            "bootstrapped": True,
            "postings": {pid: {"first_seen": now} for pid in state},
        }
    state.setdefault("postings", {})
    state.setdefault("bootstrapped", bool(state["postings"]))
    return state


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def prune(state: dict, days: int) -> int:
    if not days or days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale = []
    for pid, meta in state["postings"].items():
        try:
            first_seen = datetime.fromisoformat(str(meta.get("first_seen", "")))
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=timezone.utc)
        except ValueError:
            continue  # unparseable timestamp: keep it, safer than re-notifying
        if first_seen < cutoff:
            stale.append(pid)
    for pid in stale:
        del state["postings"][pid]
    return len(stale)


def main() -> int:
    parser = argparse.ArgumentParser(description="Job alert bot")
    parser.add_argument("--dry-run", action="store_true",
                        help="log what would be sent; never POST, never write state")
    parser.add_argument("--reseed", action="store_true",
                        help="force a silent re-seed of state (no notifications)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )

    sources_config = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))
    locations_config = yaml.safe_load(LOCATIONS_PATH.read_text(encoding="utf-8"))
    settings = sources_config.get("settings", {}) or {}

    # Optional: a missing sponsor list degrades to the old behavior (every
    # unknown-sponsorship US role is "unverified") rather than killing the run.
    if SPONSORS_PATH.exists():
        sponsors_config = yaml.safe_load(SPONSORS_PATH.read_text(encoding="utf-8")) or {}
    else:
        log.warning("%s not found -- no known-sponsor badging this run", SPONSORS_PATH.name)
        sponsors_config = {}

    state = load_state()
    bootstrapping = args.reseed or not state.get("bootstrapped", False)

    # --- fetch ---
    all_postings = []
    for source in sources_config.get("sources", []):
        all_postings.extend(fetch_source(source))

    if not all_postings:
        log.error("no postings from any source -- aborting without touching state")
        return 1
    log.info("fetched %d postings from %d sources",
             len(all_postings), len(sources_config.get("sources", [])))

    # --- filter ---
    matches = filter_postings(all_postings, locations_config, settings, sponsors_config)

    # --- dedupe (cross-source too: same id from two repos collapses here) ---
    seen_ids = set(state["postings"])
    new_by_id: dict[str, dict] = {}
    for posting in matches:
        if posting["id"] not in seen_ids and posting["id"] not in new_by_id:
            new_by_id[posting["id"]] = posting
    new_postings = sorted(
        new_by_id.values(), key=lambda p: p.get("date_posted", ""), reverse=True
    )
    log.info("%d matches, %d new after dedupe", len(matches), len(new_postings))

    now_iso = datetime.now(timezone.utc).isoformat()

    # --- bootstrap: record everything, notify about nothing ---
    if bootstrapping:
        log.info("BOOTSTRAP: seeding %d ids silently (no Discord messages sent)",
                 len(new_postings))
        if args.dry_run:
            log.info("[dry-run] state not written")
            return 0
        for posting in new_postings:
            state["postings"][posting["id"]] = {
                "first_seen": now_iso,
                "company": posting["company"],
                "title": posting["title"],
            }
        state["bootstrapped"] = True
        prune(state, settings.get("prune_after_days", 90))
        save_state(state)
        log.info("seeded. Future runs will notify only on new postings.")
        return 0

    # --- notify ---
    if not new_postings:
        log.info("nothing new this run")
        if not args.dry_run:
            removed = prune(state, settings.get("prune_after_days", 90))
            if removed:
                log.info("pruned %d stale ids", removed)
                save_state(state)
        return 0

    cap = settings.get("max_notifications_per_run", 60)
    to_send = new_postings
    overflow = []
    if cap and len(new_postings) > cap:
        log.warning("%d new postings exceeds cap of %d -- sending %d, deferring the rest",
                    len(new_postings), cap, cap)
        to_send, overflow = new_postings[:cap], new_postings[cap:]

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    sent = notify(webhook_url, to_send, dry_run=args.dry_run)
    log.info("notified on %d/%d postings", sent, len(to_send))

    if args.dry_run:
        log.info("[dry-run] state not written")
        return 0

    # Only mark what actually went out. A failed batch stays unseen and is
    # retried next run rather than being silently lost.
    for posting in to_send[:sent]:
        state["postings"][posting["id"]] = {
            "first_seen": now_iso,
            "company": posting["company"],
            "title": posting["title"],
        }
    if overflow:
        log.info("%d postings deferred to the next run", len(overflow))

    removed = prune(state, settings.get("prune_after_days", 90))
    if removed:
        log.info("pruned %d stale ids", removed)
    save_state(state)
    log.info("state now tracks %d ids", len(state["postings"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
