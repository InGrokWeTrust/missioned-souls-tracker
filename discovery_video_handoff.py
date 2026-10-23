"""Bounded discovery-to-reaction handoff: never treat a search hit as a reaction.

The discovery workflow saves only video IDs and dates. The notification tracker
independently verifies uploader, title/description, publication status,
duration, YouTube thumbnail, and delivery history before queuing or sending.
"""
import re
from datetime import datetime, timedelta, timezone

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PER_CHANNEL_LIMIT = 12
DISCOVERY_LOOKBACK_HOURS = 24
HANDOFF_RETENTION_HOURS = 48
MAX_HANDOFF_IDS_PER_SCAN = 100


def utc(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def active_candidates(info, *, now=None):
    """Return recent, validated IDs only (never extend old records' expiry)."""
    if not isinstance(info, dict):
        return []
    now = now or datetime.now(timezone.utc)
    added = utc(info.get("added_at"))
    if not added or not (timedelta(0) <= now - added <= timedelta(hours=HANDOFF_RETENTION_HOURS)):
        return []
    output = []
    seen = set()
    entries = info.get("discovery_videos", [])
    if not isinstance(entries, list):
        return []
    for entry in entries[:PER_CHANNEL_LIMIT]:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("video_id")
        published = utc(entry.get("published_at"))
        discovered = utc(entry.get("discovered_at"))
        if (not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid)
                or not published or not discovered or vid in seen
                or not (timedelta(0) <= now - discovered <= timedelta(hours=HANDOFF_RETENTION_HOURS))
                or not (timedelta(0) <= discovered - published <= timedelta(hours=DISCOVERY_LOOKBACK_HOURS))):
            continue
        output.append(entry)
        seen.add(vid)
    return output


def add_candidates(info, discovered_videos, *, now=None):
    """Store at most 12 directly found video IDs on newly registered channels."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(info, dict):
        return 0
    # Protect established channels from new unsolicited historical backfill.
    added = utc(info.get("added_at"))
    if not added or not (timedelta(0) <= now - added <= timedelta(hours=3)):
        return 0
    candidates = active_candidates(info, now=now)
    known = {entry["video_id"] for entry in candidates}
    additions = 0
    for entry in discovered_videos or []:
        if len(candidates) >= PER_CHANNEL_LIMIT:
            break
        if not isinstance(entry, dict):
            continue
        vid = entry.get("video_id")
        published = utc(entry.get("published_at"))
        if (not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid)
                or not published or vid in known
                or not (timedelta(0) <= now - published <= timedelta(hours=DISCOVERY_LOOKBACK_HOURS))):
            continue
        candidates.append({"video_id":vid, "published_at":published.isoformat(),
                           "discovered_at":now.isoformat()})
        known.add(vid)
        additions += 1
    if candidates:
        info["discovery_videos"] = candidates
    return additions


def handoff_for_scan(tracked, *, now=None):
    """Bound the YouTube IDs added to a normal reaction scan to 100."""
    now = now or datetime.now(timezone.utc)
    found = []
    for channel_id, info in tracked.items():
        for entry in active_candidates(info, now=now):
            if len(found) >= MAX_HANDOFF_IDS_PER_SCAN:
                return found
            found.append((channel_id, entry["video_id"]))
    return found
