"""Weekly liveness message.

This bot's healthy state is silence, which is indistinguishable from a bot
that died — exhausted Actions minutes, a source gone permanently 404, a
revoked webhook. The heartbeat makes silence informative.

Also estimates Actions minutes used. GitHub's /timing endpoint reports
billable 0ms on the Free tier, so it's useless here; instead we count runs
and apply GitHub's own billing rule (every job rounds UP to a whole minute).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from notify_discord import _post_with_retry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "seen.json"

# GitHub Free: 2,000 Actions minutes/month for private repos. Public repos
# get unlimited standard-runner minutes, so the quota block is skipped there.
FREE_TIER_MINUTES = 2000

COLOR_OK = 0x2ECC71
COLOR_WARN = 0xE67E22
COLOR_FAIL = 0xE74C3C

log = logging.getLogger("heartbeat")


def _api(path: str, token: str, params: dict | None = None) -> dict | None:
    try:
        response = requests.get(
            f"https://api.github.com/{path.lstrip('/')}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            params=params or {},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("github api %s failed: %s", path, exc)
        return None


def gather_state() -> dict:
    if not STATE_PATH.exists():
        return {"tracked": 0, "new_week": 0, "error": "seen.json missing"}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"tracked": 0, "new_week": 0, "error": f"seen.json unreadable: {exc}"}

    postings = state.get("postings", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    new_week = 0
    for meta in postings.values():
        try:
            first_seen = datetime.fromisoformat(str(meta.get("first_seen", "")))
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=timezone.utc)
            if first_seen >= cutoff:
                new_week += 1
        except ValueError:
            continue
    return {
        "tracked": len(postings),
        "new_week": new_week,
        "bootstrapped": state.get("bootstrapped", False),
    }


def gather_actions(repo: str, token: str) -> dict:
    """Estimate minutes used this billing month and check the last run."""
    result: dict = {}
    if not repo or not token:
        return result

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    runs = _api(
        f"repos/{repo}/actions/runs",
        token,
        {"created": f">={month_start.date().isoformat()}", "per_page": 1},
    )
    if runs is not None:
        count = runs.get("total_count", 0)
        # Every job bills as a whole minute, and each run here is one job.
        result["runs_this_month"] = count
        result["minutes_used"] = count
        result["minutes_remaining"] = max(FREE_TIER_MINUTES - count, 0)

    sample = _api(f"repos/{repo}/actions/runs", token, {"per_page": 20})
    if sample:
        # Duration must come from /timing's run_duration_ms (actual execution),
        # not updated_at - run_started_at: the latter includes queue time and
        # overstates billing. Sample a handful of runs rather than all of them.
        durations, billed_minutes = [], []
        for run in sample.get("workflow_runs", [])[:8]:
            timing = _api(f"repos/{repo}/actions/runs/{run['id']}/timing", token)
            if not timing:
                continue
            seconds = (timing.get("run_duration_ms") or 0) / 1000.0
            if seconds <= 0:
                continue
            billed_minutes.append(max(1, math.ceil(seconds / 60)))
            # Only OUR workflows inform the "runs are getting slow" warning.
            # Dependabot's graph-update job is slower and not ours to tune.
            if run.get("name") in ("check-jobs", "heartbeat"):
                durations.append(seconds)

        if durations:
            result["max_duration_s"] = round(max(durations))
            result["avg_duration_s"] = round(sum(durations) / len(durations))

        # Scale the sampled per-run billing up to the month's run count.
        if billed_minutes and result.get("runs_this_month"):
            avg_billed = sum(billed_minutes) / len(billed_minutes)
            used = math.ceil(result["runs_this_month"] * avg_billed)
            result["minutes_used"] = used
            result["minutes_remaining"] = max(FREE_TIER_MINUTES - used, 0)

        for run in sample.get("workflow_runs", []):
            if run.get("name") == "check-jobs" and run.get("status") == "completed":
                result["last_check_conclusion"] = run.get("conclusion")
                result["last_check_at"] = run.get("updated_at")
                break

        # Consecutive recent failures matter more than any single one.
        recent = [
            r.get("conclusion")
            for r in sample.get("workflow_runs", [])
            if r.get("name") == "check-jobs" and r.get("status") == "completed"
        ][:5]
        result["recent_failures"] = sum(1 for c in recent if c and c != "success")

    return result


def build_embed(state: dict, actions: dict, is_private: bool) -> dict:
    problems, warnings = [], []

    if state.get("error"):
        problems.append(state["error"])
    if actions.get("recent_failures"):
        problems.append(f"{actions['recent_failures']} of the last 5 checks failed")
    if actions.get("last_check_conclusion") not in (None, "success"):
        problems.append(f"last check: {actions['last_check_conclusion']}")

    remaining = actions.get("minutes_remaining")
    if is_private and remaining is not None:
        if remaining <= 0:
            problems.append("Actions minutes exhausted — the bot has stopped")
        elif remaining < FREE_TIER_MINUTES * 0.15:
            warnings.append(f"only ~{remaining} Actions minutes left this month")

    max_duration = actions.get("max_duration_s")
    if is_private and max_duration and max_duration > 45:
        warnings.append(
            f"runs reaching {max_duration}s — crossing 60s doubles minute usage"
        )

    lines = [
        f"**{state.get('tracked', 0)}** postings tracked · "
        f"**{state.get('new_week', 0)}** new in the last 7 days",
    ]

    if state.get("new_week", 0) == 0 and not problems:
        lines.append("_No new matches this week. The bot is running fine — "
                     "EU new-grad postings are genuinely sparse._")

    if is_private and actions.get("minutes_used") is not None:
        used = actions["minutes_used"]
        pct = round(100 * used / FREE_TIER_MINUTES)
        bar_filled = min(int(pct / 10), 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        lines.append(
            f"\n**Actions minutes** (est.)\n`{bar}` {used}/{FREE_TIER_MINUTES} "
            f"used this month · ~{actions['minutes_remaining']} left"
        )
        if actions.get("avg_duration_s"):
            lines.append(f"_avg run {actions['avg_duration_s']}s, "
                         f"max {actions.get('max_duration_s')}s_")
    elif not is_private:
        lines.append("\n**Actions minutes** · unlimited (public repo)")

    if warnings:
        lines.append("\n⚠️ " + "\n⚠️ ".join(warnings))
    if problems:
        lines.append("\n🔴 " + "\n🔴 ".join(problems))

    if problems:
        color, status = COLOR_FAIL, "needs attention"
    elif warnings:
        color, status = COLOR_WARN, "running, with warnings"
    else:
        color, status = COLOR_OK, "healthy"

    return {
        "title": f"🫀 Job Alert Bot — weekly heartbeat ({status})",
        "description": "\n".join(lines)[:4096],
        "color": color,
        "footer": {"text": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly heartbeat")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stdout)

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    is_private = os.environ.get("REPO_VISIBILITY", "private").lower() != "public"

    state = gather_state()
    actions = gather_actions(repo, token) if repo and token else {}
    if not actions:
        log.info("no Actions data (missing token/repo, or API unreachable)")

    embed = build_embed(state, actions, is_private)
    log.info("heartbeat: %s", embed["title"])
    log.info("%s", embed["description"].replace("\n", " | "))

    if args.dry_run:
        log.info("[dry-run] not sending")
        return 0

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        log.error("DISCORD_WEBHOOK_URL not set")
        return 1

    if _post_with_retry(webhook_url, {"embeds": [embed]}):
        log.info("heartbeat sent")
        return 0
    log.error("heartbeat failed to send")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
