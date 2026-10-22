"""Versioned, crash-safe notification state for the reaction tracker.

Older bookmark files contain only 'last_published_at'. Their timestamp is used
once as a migration cutoff, after which video IDs become the deduplication key.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

STATE_VERSION = 2
LOOKBACK_DAYS = 7
MAX_SEEN_IDS = 20000
MAX_PENDING = 2000


class StateError(RuntimeError):
    pass


def _empty_state(*, bootstrap=False, legacy_cutoff=None):
    return {
        "version": STATE_VERSION,
        "last_published_at": legacy_cutoff,
        "seen_video_ids": [],
        "known_channel_ids": [],
        "pending": [],
        "ocr_results": {},
        "_bootstrap": bootstrap,
        "_migrate_legacy": legacy_cutoff is not None,
    }


def load_state(path, *, allow_bootstrap=False):
    """Refuse missing or invalid bookmarks, unless bootstrap is explicit."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError as exc:
        if allow_bootstrap:
            print("⚠️ Explicit bootstrap requested; existing eligible videos will be marked seen.")
            return _empty_state(bootstrap=True)
        raise StateError(
            f"Missing bookmark '{path}'. No videos were sent. Recover the previous "
            "artifact or explicitly set BOOTSTRAP_STATE_IF_MISSING=true for ONE seed-only run."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StateError(f"Unreadable bookmark '{path}'; refusing to reset notification history.") from exc

    if not isinstance(payload, dict):
        raise StateError("Bookmark must be a JSON object.")

    if payload.get("version") == STATE_VERSION:
        for field in ("seen_video_ids", "known_channel_ids", "pending"):
            if not isinstance(payload.get(field), list):
                raise StateError(f"Bookmark field '{field}' must be a list.")
        if not all(isinstance(v, str) and v for v in payload["seen_video_ids"]):
            raise StateError("Bookmark contains invalid video IDs.")
        if not all(isinstance(c, str) and c for c in payload["known_channel_ids"]):
            raise StateError("Bookmark contains invalid channel IDs.")
        for video in payload["pending"]:
            if not isinstance(video, dict) or not all(
                isinstance(video.get(k), str) and video[k] for k in
                ("video_id", "channel_id", "published_at", "title", "url")
            ):
                raise StateError("Bookmark contains an invalid pending video.")
        if not isinstance(payload.get("ocr_results", {}), dict):
            raise StateError("Bookmark OCR cache has invalid schema.")
        payload.setdefault("ocr_results", {})
        payload["_bootstrap"] = False
        payload["_migrate_legacy"] = False
        return payload

    cutoff = payload.get("last_published_at")
    if isinstance(cutoff, str) and cutoff:
        print("📦 Legacy timestamp bookmark detected; migrating safely to ID-based state.")
        return _empty_state(legacy_cutoff=cutoff)

    raise StateError("Bookmark version is unknown or required fields are missing; refusing to reset.")


def save_state(path, state):
    """Atomically persist only public state fields (never internal migration flags)."""
    persistent = {key: state.get(key) for key in (
        "version", "last_published_at", "seen_video_ids",
        "known_channel_ids", "pending", "ocr_results"
    )}
    directory = os.path.dirname(os.path.abspath(path))
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory,
            prefix=".tracker-state-", suffix=".tmp", delete=False
        ) as f:
            temp_path = f.name
            json.dump(persistent, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _utc(iso):
    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _remember(state, video_id):
    seen = state["seen_video_ids"]
    if video_id not in seen:
        seen.append(video_id)
    if len(seen) > MAX_SEEN_IDS:
        del seen[:-MAX_SEEN_IDS]


def plan_notifications(state, videos, tracked_channel_ids, *, now=None, channel_added_at=None):
    """Queue recent unseen reactions, preserving pending items across scans.

    An explicit bootstrap seeds current uploads with no Discord output.
    Migrated timestamp-only bookmarks seed videos at/before the old cutoff.
    New channels are seeded on first sight (no backfill).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    channel_added_at = channel_added_at or {}
    known = set(state["known_channel_ids"])
    current = set(tracked_channel_ids)
    new_channels = current - known
    migration = state.get("_migrate_legacy", False)
    bootstrap = state.get("_bootstrap", False)
    old_cutoff = _utc(state["last_published_at"]) if migration else None

    seen = set(state["seen_video_ids"])
    queued = {v["video_id"] for v in state["pending"]}
    seeded = 0
    enqueued = 0
    for video in sorted(videos, key=lambda v: (v["published_at"], v["video_id"])):
        vid = video["video_id"]
        if vid in seen or vid in queued:
            continue
        if not video.get("channel_id"):
            raise StateError("Reaction has no channel_id; cannot classify initial channel backfill.")
        published = _utc(video["published_at"])
        registered_at = channel_added_at.get(video["channel_id"])
        first_seen_channel = not migration and video["channel_id"] in new_channels
        should_seed = (
            bootstrap
            or (migration and published <= old_cutoff)
            or (first_seen_channel and (
                not registered_at or published <= _utc(registered_at)
            ))
        )
        if should_seed:
            _remember(state, vid)
            seen.add(vid)
            seeded += 1
        elif published >= cutoff:
            if len(state["pending"]) >= MAX_PENDING:
                raise StateError("Pending queue is full; refusing to drop videos.")
            state["pending"].append(video)
            queued.add(vid)
            enqueued += 1
        # Old unannounced videos outside the lookback are deliberately not backfilled.
    state["known_channel_ids"] = sorted(known | current)
    state["pending"].sort(key=lambda v: (v["published_at"], v["video_id"]))
    state["_migrate_legacy"] = False
    state["_bootstrap"] = False
    print(f"📬 Queued {enqueued} new, seeded {seeded}, pending {len(state['pending'])}.")
    return enqueued, seeded


def read_live_sent_ids(path):
    """Read recently announced live stream IDs for cross-workflow deduplication.

    A missing live artifact means no cross-workflow history is available.
    We warn instead of assuming an empty file contains authoritative history.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        print("::warning::Live stream history artifact unavailable; archived VOD dedup is partial.")
        return set()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StateError("Live history artifact is corrupt; refusing ambiguous duplicate checks.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sent_video_ids"), list):
        raise StateError("Live history artifact has invalid schema.")
    return {v for v in payload["sent_video_ids"] if isinstance(v, str) and v}


def reconcile_live_notifications(state, sent_live_ids):
    """Remove duplicates already announced by the live-stream workflow."""
    removed = 0
    remaining = []
    for video in state["pending"]:
        if video["video_id"] in sent_live_ids:
            _remember(state, video["video_id"])
            removed += 1
        else:
            remaining.append(video)
    state["pending"] = remaining
    if removed:
        print(f"🔁 Skipped {removed} archived stream notifications already announced live.")
    return removed


def acknowledge(state, video):
    """Call only following a confirmed webhook success."""
    vid = video["video_id"]
    state["pending"] = [v for v in state["pending"] if v["video_id"] != vid]
    _remember(state, vid)
    published = video["published_at"]
    previous = state.get("last_published_at")
    if not previous or _utc(published) > _utc(previous):
        state["last_published_at"] = published
