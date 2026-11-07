"""Append-only delivered video ID history stored on a dedicated GitHub branch.

Actions artifacts remain working queue snapshots. The history branch is a
long-lived deduplication source and is never reset from a single artifact.
"""
import base64
import json
import os
import time
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


# This optional version-1 field is an unresolved pre-Discord attempt, NOT
# evidence of successful delivery. It survives an Actions runner/artifact loss
# because it is committed to the same permanent branch as the ID history.
REMOTE_ATTEMPT_KEY = "unconfirmed_discord_video_id"


def _validate_video_id(video_id):
    if not isinstance(video_id, str) or not 0 < len(video_id) <= 128:
        raise ValueError("Invalid durable delivery video ID")
    return video_id


def _read_remote_document(kind):
    """Read validated complete history, preserving optional attempt state."""
    if kind not in KINDS:
        raise ValueError("Unknown history stream")
    path = quote(KINDS[kind], safe="/") + "?ref=" + quote(HISTORY_BRANCH)
    response = _api_request("GET", "contents/" + path)
    if response.get("type") != "file" or not response.get("sha"):
        raise ValueError("Durable history GitHub file is missing or invalid")
    decoded = base64.b64decode(response["content"].replace("\\n", ""), validate=True)
    state = json.loads(decoded)
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Durable history has an unknown schema")
    state["sent_video_ids"] = _ids(state.get("sent_video_ids"))
    marker = state.get(REMOTE_ATTEMPT_KEY)
    if marker is not None:
        _validate_video_id(marker)
    return state, response["sha"]


def _read_remote(kind):
    """Legacy read API; refuse to hide an unresolved remote delivery attempt.

    The existing save_ids() compatibility method must never erase a marker
    that was written by another runner, even when it appends different IDs.
    """
    state, sha = _read_remote_document(kind)
    if state.get(REMOTE_ATTEMPT_KEY) is not None:
        raise RuntimeError(
            "Unreconciled durable Discord delivery attempt; "
            "review GitHub notification history and Discord before restarting"
        )
    return state["sent_video_ids"], sha

def read_ids(kind):
    """Read the confirmed remote ledger; fail closed on missing/invalid state."""
    if not HISTORY_ENABLED:
        raise RuntimeError("Durable notification history must be enabled")
    remote, _ = _read_remote(kind)
    return set(remote)


# A failed GitHub write can have committed despite an HTTP 5xx response.
# Read the authoritative remote ledger again before retrying the PUT.
RETRYABLE_HISTORY_STATUS = frozenset({500, 502, 503, 504})
MAX_HISTORY_WRITE_ATTEMPTS = 5


def save_ids(kind, ids):
    """Persist append-only IDs, reconciling ambiguous transient API failures.

    A Discord webhook is NEVER retried here. Re-fetch remote history before
    each PUT; if a previous write actually committed, accept that evidence.
    Exhausted/transiently unreadable history still fails closed for the
    caller's durable delivery-attempt marker.
    """
    if os.getenv("DRY_RUN", "false").strip().lower() == "true":
        raise RuntimeError("DRY RUN: durable history writes are disabled")
    if not HISTORY_ENABLED:
        raise RuntimeError("Durable notification history must be enabled")
    incoming = _ids(ids)
    for attempt in range(MAX_HISTORY_WRITE_ATTEMPTS):
        try:
            remote, sha = _read_remote(kind)
            combined = merge_ids(remote, incoming)
            if combined == remote:
                if attempt:
                    print("✅ Durable " + kind + " history already contains confirmed ID(s)")
                return combined
            value = {"version": 1, "sent_video_ids": combined}
            encoded = base64.b64encode((json.dumps(value, indent=2) + "\n").encode()).decode()
            body = {
                "message": "Record confirmed " + kind + " notifications",
                "branch": HISTORY_BRANCH,
                "sha": sha,
                "content": encoded,
            }
            _api_request("PUT", "contents/" + KINDS[kind], body)
            print("💾 Durable " + kind + " history saved: " + str(len(combined)) + " IDs")
            return combined
        except HTTPError as error:
            code = error.code
            if code not in (409, 422) and code not in RETRYABLE_HISTORY_STATUS:
                raise
            if attempt == MAX_HISTORY_WRITE_ATTEMPTS - 1:
                raise
            if code in RETRYABLE_HISTORY_STATUS:
                delay = min(2 ** attempt, 4)
                print(f"⚠️ Transient GitHub history HTTP {code}; rechecking ledger after {delay}s")
                time.sleep(delay)
            else:
                print("⚠️ Concurrent GitHub history update; re-reading ledger")
    raise RuntimeError("Unable to reconcile durable history after retries")


# The notification worktrees use the same GitHub history branch for both
# notification kinds. All production senders reserve BEFORE their webhook
# and confirm/reject AFTER the HTTP response. Normal workflow concurrency
# serializes the two notification jobs.
def _require_durable_writes():
    if os.getenv("DRY_RUN", "false").strip().lower() == "true":
        raise RuntimeError("DRY RUN: durable history writes are disabled")
    if not HISTORY_ENABLED:
        raise RuntimeError("Durable notification history must be enabled")


def _put_remote_document(kind, state, sha, action):
    value = json.dumps(state, indent=2) + "\\n"
    body = {
        "message": action + " " + kind + " Discord delivery",
        "branch": HISTORY_BRANCH,
        "sha": sha,
        "content": base64.b64encode(value.encode("utf-8")).decode("ascii"),
    }
    return _api_request("PUT", "contents/" + KINDS[kind], body)


def reserve_delivery(kind, video_id):
    """Persist an unresolved attempt remotely BEFORE any Discord POST.

    If GitHub gives an ambiguous response, fail closed even if the marker
    might have committed. Only a completed, verified reserve can POST.
    """
    _require_durable_writes()
    _validate_video_id(video_id)
    if kind not in KINDS:
        raise ValueError("Unknown notification history kind")
    documents = {}
    for stream in KINDS:
        documents[stream] = _read_remote_document(stream)
        data, _ = documents[stream]
        if data.get(REMOTE_ATTEMPT_KEY) is not None:
            raise RuntimeError(
                "Unreconciled durable Discord delivery attempt; "
                "verify Discord and permanent history before another POST"
            )
        if video_id in data["sent_video_ids"]:
            raise RuntimeError("Video is already durably confirmed; duplicate POST blocked")
    data, sha = documents[kind]
    proposed = dict(data)
    proposed[REMOTE_ATTEMPT_KEY] = video_id
    _put_remote_document(kind, proposed, sha, "Reserve")
    # Verify the marker exists in authoritative remote state before POST.
    persisted, _ = _read_remote_document(kind)
    if (persisted.get(REMOTE_ATTEMPT_KEY) != video_id
            or video_id in persisted["sent_video_ids"]):
        raise RuntimeError("Durable Discord reservation not verified; POST blocked")
    print("🔐 Durable " + kind + " delivery attempt reserved")
    return video_id


def _settle_delivery(kind, video_id, *, accepted):
    _require_durable_writes()
    _validate_video_id(video_id)
    if kind not in KINDS:
        raise ValueError("Unknown notification history kind")
    for attempt in range(MAX_HISTORY_WRITE_ATTEMPTS):
        try:
            state, sha = _read_remote_document(kind)
            marker = state.get(REMOTE_ATTEMPT_KEY)
            ids = state["sent_video_ids"]
            if marker is None:
                if accepted and video_id in ids:
                    return ids  # A previous ambiguous PUT already committed.
                if not accepted and video_id not in ids:
                    return ids  # Explicit rejection was already cleared.
                raise RuntimeError("No matching remote delivery attempt; refusing to infer result")
            if marker != video_id:
                raise RuntimeError("Different remote delivery attempt is active; refusing mutation")
            if not accepted and video_id in ids:
                raise RuntimeError("Cannot reject a durably confirmed delivery")
            updated = dict(state)
            updated.pop(REMOTE_ATTEMPT_KEY)
            updated["sent_video_ids"] = merge_ids(ids, [video_id]) if accepted else ids
            _put_remote_document(
                kind, updated, sha, "Confirm" if accepted else "Reject"
            )
            print("💾 Durable " + kind + " history " +
                  ("confirmed" if accepted else "released") + " for one video")
            return updated["sent_video_ids"]
        except HTTPError as error:
            if error.code not in (409, 422) and error.code not in RETRYABLE_HISTORY_STATUS:
                raise
            if attempt == MAX_HISTORY_WRITE_ATTEMPTS - 1:
                raise
            if error.code in RETRYABLE_HISTORY_STATUS:
                delay = min(2 ** attempt, 4)
                print(f"⚠️ Transient GitHub delivery-state HTTP {error.code}; "
                      f"rechecking after {delay}s")
                time.sleep(delay)
            else:
                print("⚠️ Concurrent delivery-history update; rereading ledger")
    raise RuntimeError("Unable to settle durable Discord attempt")


def confirm_delivery(kind, video_id):
    """After HTTP 204, append delivered ID AND clear marker in ONE GitHub PUT."""
    return _settle_delivery(kind, video_id, accepted=True)


def reject_delivery(kind, video_id):
    """Only for an explicitly rejected webhook response; never on timeout."""
    return _settle_delivery(kind, video_id, accepted=False)
