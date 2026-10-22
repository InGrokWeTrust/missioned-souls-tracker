"""Append-only delivered video ID history stored on a dedicated GitHub branch.

Actions artifacts remain working queue snapshots. The history branch is a
long-lived deduplication source and is never reset from a single artifact.
"""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

HISTORY_BRANCH = "notification-history"
KINDS = {"reactions": "history/reactions.json", "live": "history/live.json"}
HISTORY_ENABLED = os.getenv("HISTORY_ENABLED", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"


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


def save_ids(kind, ids):
    """Persist a growing union using GitHub's SHA compare-and-swap."""
    if not HISTORY_ENABLED or DRY_RUN:
        return _ids(ids)
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


def _atomic_write(path, payload):
    path = Path(path)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".history-hydrate-", delete=False) as f:
            tmp = f.name
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def hydrate(kind, local_file):
    """Merge saved artifact with durable history before starting a tracker.

    Always fail closed if the artifact or durable ledger is missing/corrupt.
    Dry runs only inspect history and never change bookmarks (even local copies).
    """
    local_file = Path(local_file)
    if not local_file.is_file():
        raise RuntimeError("Saved state artifact unavailable: refusing to seed an empty tracker")
    local = json.loads(local_file.read_text(encoding="utf-8"))
    if not isinstance(local, dict):
        raise ValueError("Invalid saved state")
    if kind == "reactions":
        if local.get("version") != 2 or not isinstance(local.get("pending"), list):
            raise ValueError("Invalid reaction state for durable-history restoration")
        field = "seen_video_ids"
    elif kind == "live":
        field = "sent_video_ids"
    else:
        raise ValueError("Unknown history stream")
    local_ids = _ids(local.get(field))
    remote_ids, _ = _read_remote(kind)
    combined = merge_ids(remote_ids, local_ids)
    if DRY_RUN:
        print("🧪 DRY RUN: inspected " + kind + " durable history; no state or history writes")
        return combined
    save_ids(kind, combined)  # Seed/reconcile durable history BEFORE any Discord delivery.
    if combined != local_ids:
        local[field] = combined
        if kind == "reactions":
            local["pending"] = [
                video for video in local["pending"]
                if video.get("video_id") not in combined
            ]
        _atomic_write(local_file, local)
    print("✅ Hydrated " + kind + " state from durable history: " + str(len(combined)) + " IDs")
    return combined


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "hydrate" or sys.argv[2] not in KINDS:
        raise SystemExit("Usage: python notification_history.py hydrate reactions|live FILE")
    hydrate(sys.argv[2], sys.argv[3])
