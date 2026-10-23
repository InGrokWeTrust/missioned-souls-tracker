"""Append-only delivered video ID history stored on a dedicated GitHub branch.

Actions artifacts remain working queue snapshots. The history branch is a
long-lived deduplication source and is never reset from a single artifact.
"""
import base64
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

HISTORY_BRANCH = "notification-history"
KINDS = {"reactions": "history/reactions.json", "live": "history/live.json"}
HISTORY_ENABLED = os.getenv("HISTORY_ENABLED", "false").lower() == "true"


def _ids(values):
    if not isinstance(values, list) or not all(
        isinstance(value, str) and 0 < len(value) <= 128 for value in values
    ):
        raise ValueError("Invalid durable history video ID list")
    return list(dict.fromkeys(values))


def merge_ids(*collections):
    """Stable append-only union: keep older IDs and append only missing IDs."""
    result = []
    for values in collections:
        result.extend(_ids(values))
    return list(dict.fromkeys(result))


def _api_request(method, path, payload=None):
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo or "/" not in repo:
        raise RuntimeError("Durable history requires GITHUB_TOKEN and GITHUB_REPOSITORY")
    base = os.getenv("GITHUB_API_URL", "https://api.github.com")
    url = base.rstrip("/") + "/repos/" + repo + "/" + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, headers=headers, method=method), timeout=25) as response:
        return json.load(response)


def _read_remote(kind):
    if kind not in KINDS:
        raise ValueError("Unknown history stream")
    path = quote(KINDS[kind], safe="/") + "?ref=" + quote(HISTORY_BRANCH)
    response = _api_request("GET", "contents/" + path)
    if response.get("type") != "file" or not response.get("sha"):
        raise ValueError("Durable history GitHub file is missing or invalid")
    decoded = base64.b64decode(response["content"].replace("\n", ""), validate=True)
    state = json.loads(decoded)
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Durable history has an unknown schema")
    return _ids(state.get("sent_video_ids")), response["sha"]


def read_ids(kind):
    """Read the confirmed remote ledger; fail closed on missing/invalid state."""
    if not HISTORY_ENABLED:
        raise RuntimeError("Durable notification history must be enabled")
    remote, _ = _read_remote(kind)
    return set(remote)


def save_ids(kind, ids):
    """Persist a growing union using GitHub's SHA compare-and-swap."""
    # Defense in depth: even accidental direct calls must not mutate
    # the confirmed-delivery ledger in a dry-run workflow.
    if os.getenv("DRY_RUN", "false").strip().lower() == "true":
        raise RuntimeError("DRY RUN: durable history writes are disabled")
    if not HISTORY_ENABLED:
        raise RuntimeError("Durable notification history must be enabled")
    incoming = _ids(ids)
    for attempt in range(5):
        remote, sha = _read_remote(kind)
        combined = merge_ids(remote, incoming)
        if combined == remote:
            return combined
        value = {"version": 1, "sent_video_ids": combined}
        encoded = base64.b64encode((json.dumps(value, indent=2) + "\n").encode()).decode()
        body = {
            "message": "Record confirmed " + kind + " notifications",
            "branch": HISTORY_BRANCH,
            "sha": sha,
            "content": encoded,
        }
        try:
            _api_request("PUT", "contents/" + KINDS[kind], body)
            print("💾 Durable " + kind + " history saved: " + str(len(combined)) + " IDs")
            return combined
        except HTTPError as error:
            if error.code not in (409, 422) or attempt == 4:
                raise
    raise RuntimeError("Unable to reconcile durable history after retries")


