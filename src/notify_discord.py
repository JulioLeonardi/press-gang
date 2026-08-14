"""Discord webhook delivery: batched embeds with rate-limit-aware retries."""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

EMBEDS_PER_MESSAGE = 10  # Discord's hard cap
MAX_RETRIES = 4
TIMEOUT = 20

COLOR_EU = 0x3B88C3
COLOR_US = 0x2ECC71
COLOR_UNVERIFIED = 0xE67E22


def build_embed(posting: dict) -> dict:
    company = posting.get("company", "Unknown")
    title = posting.get("title", "Unknown role")
    location = posting.get("location", "Unspecified")
    reason = posting.get("match_reason", "")
    unverified = posting.get("unverified", False)

    bits = [f"\U0001F4CD {location}"]
    if posting.get("sponsorship_flag") == "yes":
        bits.append("\U0001F6C2 Sponsors internationals")
    if unverified:
        bits.append("⚠️ Sponsorship unconfirmed")
    bits.append(f"_{reason} · {posting.get('source_repo', '')}_")

    embed = {
        "title": f"{company} — {title}"[:256],
        "description": " · ".join(bits)[:4096],
        "color": COLOR_UNVERIFIED if unverified else (COLOR_EU if reason.startswith("EU") else COLOR_US),
        "footer": {"text": f"Posted {_pretty_date(posting.get('date_posted', ''))}"},
    }
    # Discord rejects the whole payload if `url` is present but not a valid URL.
    url = posting.get("url") or ""
    if url.startswith("http"):
        embed["url"] = url
    return embed


def _pretty_date(mmddyyyy: str) -> str:
    if len(mmddyyyy) == 8 and mmddyyyy.isdigit():
        return f"{mmddyyyy[:2]}/{mmddyyyy[2:4]}/{mmddyyyy[4:]}"
    return "unknown date"


def notify(webhook_url: str, postings: list[dict], *, dry_run: bool = False) -> int:
    """Send postings as batched embeds. Returns the count successfully sent."""
    if not postings:
        return 0
    if not webhook_url and not dry_run:
        log.error("DISCORD_WEBHOOK_URL is not set -- cannot notify")
        return 0

    sent = 0
    batches = [
        postings[i : i + EMBEDS_PER_MESSAGE]
        for i in range(0, len(postings), EMBEDS_PER_MESSAGE)
    ]

    for index, batch in enumerate(batches, start=1):
        payload = {"embeds": [build_embed(p) for p in batch]}
        if dry_run:
            log.info("[dry-run] batch %d/%d: %d embeds", index, len(batches), len(batch))
            for posting in batch:
                log.info("[dry-run]   %s — %s (%s)",
                         posting.get("company"), posting.get("title"), posting.get("location"))
            sent += len(batch)
            continue

        if _post_with_retry(webhook_url, payload):
            sent += len(batch)
        else:
            log.error("batch %d/%d failed after retries -- not marking as sent", index, len(batches))
            break  # stop early; unsent ids stay unseen and retry next run

        if index < len(batches):
            time.sleep(1.0)  # stay well under the webhook rate limit

    return sent


def _post_with_retry(webhook_url: str, payload: dict) -> bool:
    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(webhook_url, json=payload, timeout=TIMEOUT)
        except requests.RequestException as exc:
            log.warning("webhook attempt %d: network error (%s)", attempt, exc)
        else:
            if response.status_code in (200, 204):
                return True
            if response.status_code == 429:
                # Discord tells us exactly how long to wait.
                try:
                    wait = float(response.json().get("retry_after", delay))
                except (ValueError, KeyError, AttributeError):
                    wait = delay
                log.warning("webhook rate-limited; sleeping %.1fs", wait)
                time.sleep(min(wait, 60.0))
                continue
            if 400 <= response.status_code < 500:
                # Malformed payload — retrying identical content won't help.
                log.error("webhook rejected (%d): %s", response.status_code, response.text[:400])
                return False
            log.warning("webhook attempt %d: HTTP %d", attempt, response.status_code)

        time.sleep(delay)
        delay *= 2
    return False
