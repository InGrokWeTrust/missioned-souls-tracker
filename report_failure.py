"""Send only actionable, sanitised production error summaries to Discord.

Generic workflow failures and GitHub run links belong in GitHub Actions,
not the reaction channel. Read-only log parsing: do not echo raw API errors,
webhook secrets, command lines, stack traces, video IDs or URLs to Discord.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

import requests


MAX_LOG_BYTES = 512_000


def read_error_log(path):
    """Read the bounded tail of a local workflow step log; absent is normal."""
    if not path:
        return ""
    try:
        p = Path(path)
        with p.open("rb") as f:
            size = f.seek(0, 2)
            f.seek(max(size - MAX_LOG_BYTES, 0))
            return f.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def classify_error(log, workflow=""):
    """Return (safe title, safe explanation), or None when not identified.

    Inspect the final traceback preferentially when one exists, so an earlier
    benign per-channel API error cannot obscure an unrelated fatal exception.
    Descriptions are predefined static strings, never copied from logs.
    """
    if not log:
        return None
    text = log
    traceback_marker = "Traceback (most recent call last):"
    if traceback_marker in text:
        text = text.rsplit(traceback_marker, 1)[-1]

    low = text.lower()
    live = "live" in (workflow or "").lower()
    task = "live-stream checker" if live else "reaction tracker"

    if any(t in low for t in ("quotaexceeded", "quota exceeded",
                             "dailylimitexceeded", "daily quota exhausted")):
        return (
            "YouTube API quota exceeded",
            f"The {task} could not complete because YouTube's API quota "
            "was exhausted. Scanning can resume after the quota resets.",
        )

    if any(t in low for t in ("api_key_invalid", "keyinvalid",
                             "api key not valid", "accessnotconfigured",
                             "invalid api key", "forbidden: api key")):
        return (
            "YouTube API key configuration error",
            f"The {task} could not access YouTube. Check the API key "
            "and API permissions in the repository settings.",
        )

    if "last_run.json" in low or "live_last_run.json" in low:
        if any(t in low for t in ("not found", "missing", "corrupt", "invalid",
                                 "jsondecodeerror", "filenotfounderror",
                                 "statepreflight", "bootstrap")):
            return (
                "Tracker bookmark cannot be restored",
                "The saved notification state is missing or invalid. "
                "Restore a valid bookmark before retrying, to avoid duplicate posts.",
            )

    if ("notification queue capacity reached" in low or
            "held-video limit reached" in low or
            "invalid pending-video bookmark" in low or
            "invalid held-video bookmark" in low or
            "invalid pending-video identity" in low or
            "invalid held-video identity" in low):
        return (
            "Reaction notification queue requires attention",
            "The tracker cannot safely update or read the pending reaction "
            "queue. Review the saved bookmark before retrying. "
            "No historical videos should be replayed manually.",
        )

    if ("durable" in low and "history" in low and
            any(t in low for t in ("unavailable", "failed", "invalid",
                                   "missing", "unknown", "unable", "requires"))):
        return (
            "Confirmed-delivery history unavailable",
            "The tracker cannot verify previously delivered video IDs. "
            "Check the notification-history branch and GitHub permissions "
            "before retrying to prevent duplicate alerts.",
        )

    if ("discord" in low or "webhook" in low) and any(
        t in low for t in ("request failed", "returned http", "delivery failed",
                          "webhook failed", "could not send",
                          "discord http", "discord live http")
    ):
        return (
            "Discord notification delivery failed",
            "The tracker could not complete a Discord delivery. "
            "Unconfirmed items should remain queued for a later attempt.",
        )

    if any(t in low for t in ("httperror 503", "httperror 502", "httperror 500",
                             "backenderror", "serviceunavailable")):
        return (
            "YouTube API temporarily unavailable",
            f"The {task} encountered a YouTube service error. "
            "The next scheduled run can retry.",
        )

    return None


def report_failure(webhook_url=None, workflow=None, run_url=None, post=None,
                   log_path=None):
    """Send a diagnosed error only; run_url intentionally ignored.

    Missing/unknown diagnostics are recorded in GitHub Actions logs alone.
    """
    name = workflow or os.environ.get("GITHUB_WORKFLOW", "Missioned Souls tracker")
    path = log_path if log_path is not None else os.environ.get("ALERT_LOG_FILE", "")
    diagnostic = classify_error(read_error_log(path), workflow=name)
    if diagnostic is None:
        print("ℹ️ No verified failure cause found; Discord health alert suppressed.")
        return False

    # Dedicated health channel only: never put image-free diagnostics
    # into the reaction notifications feed (which must remain thumbnail-only).
    url = webhook_url if webhook_url is not None else os.environ.get("ALERT_WEBHOOK_URL")
    if not url:
        print("⚠️ Dedicated ALERT_WEBHOOK_URL not configured. Diagnostic stays in GitHub logs.")
        return False

    title, explanation = diagnostic
    payload = {
        "username": "Missioned Souls Tracker Health",
        "embeds": [{
            "title": f"⚠️ {title}",
            "description": explanation,
            "color": 0xf39c12,
        }],
    }

    try:
        request = post if post is not None else requests.post
        response = request(url, json=payload, timeout=10)
        if response.status_code in (200, 204):
            print("✅ Specific, link-free failure alert delivered.")
            return True
        print(f"⚠️ Failure alert failed: HTTP {response.status_code}")
    except requests.RequestException as error:
        print(f"⚠️ Could not send failure alert: {type(error).__name__}")
    return False


if __name__ == "__main__":
    report_failure()
