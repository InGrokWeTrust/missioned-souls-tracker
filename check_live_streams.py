import os
import time
import json
import tempfile
import requests
import re
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from googleapiclient.discovery import build
from webhook_delivery import post_webhook
from thumbnail_policy import select_thumbnail, require_thumbnails
from notification_history import save_ids as save_durable_ids
from channel_exclusions import is_excluded_channel

# ================== READ CONFIG FROM ENVIRONMENT ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = (os.environ.get("CHANNEL_NAME") or "Missioned Souls")
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND") or "6")
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
LIVE_LAST_RUN_FILE = (os.environ.get("LIVE_LAST_RUN_FILE") or "live_last_run.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL and not DRY_RUN:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

# Cap on stored IDs to prevent unbounded growth
MAX_STORED_IDS = 2000
AUCKLAND_TZ = ZoneInfo('Pacific/Auckland')

# ===========================================

youtube = build('youtube', 'v3', developerKey=API_KEY)


def load_state():
    """Fail closed if the bookmark disappeared or is malformed."""
    try:
        with open(LIVE_LAST_RUN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        if os.environ.get("BOOTSTRAP_STATE_IF_MISSING", "false").lower() == "true":
            return [], "BOOTSTRAP"
        raise RuntimeError(
            "Live bookmark missing: restore the workflow artifact, or explicitly "
            "bootstrap once with BOOTSTRAP_STATE_IF_MISSING=true."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Live bookmark corrupt; refusing to reset and repost.") from exc

    if not isinstance(data, dict) or not isinstance(data.get("sent_video_ids"), list):
        raise RuntimeError("Live bookmark has an invalid schema.")
    if not all(isinstance(v, str) and v for v in data["sent_video_ids"]):
        raise RuntimeError("Live bookmark has invalid video IDs.")
    return data["sent_video_ids"], data.get("last_check")


def save_state(sent_ids):
    """Atomic state write to avoid half-written JSON after interruption."""
    if DRY_RUN:
        print("🧪 DRY RUN: live bookmark remains unchanged.")
        return
    data = {
        "sent_video_ids": sent_ids[-MAX_STORED_IDS:],
        "last_check": datetime.now(timezone.utc).isoformat()
    }
    directory = os.path.dirname(os.path.abspath(LIVE_LAST_RUN_FILE))
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory,
            prefix=".live-state-", suffix=".tmp", delete=False
        ) as f:
            path = f.name
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(path, LIVE_LAST_RUN_FILE)
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
    print(f"💾 Updated {LIVE_LAST_RUN_FILE} → {len(data['sent_video_ids'])} IDs tracked")


def humanize_ago(published_at_iso, now=None):
    if now is None:
        now = datetime.now(timezone.utc)

    try:
        ts = published_at_iso.replace('Z', '+00:00')
        published = datetime.fromisoformat(ts)
    except Exception:
        return "unknown"

    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    delta = now - published
    seconds = int(delta.total_seconds())

    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d {h}h ago" if h else f"{d}d ago"


def format_published_time(published_at_iso):
    try:
        ts = published_at_iso.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(AUCKLAND_TZ).strftime('%I:%M %p %Z').lstrip('0')
    except Exception:
        return ""


def find_active_live_streams():
    """Check search hits against live broadcast metadata before announcing."""
    print(f"🔍 Searching for ACTIVE live streams about {CHANNEL_NAME}...")

    response = youtube.search().list(
        part="snippet",
        q=f'"{CHANNEL_NAME}"',
        type="video",
        eventType="live",
        maxResults=50,
        order="date"
    ).execute()

    matches = {}
    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        title = snippet.get("title", "")
        video_id = item.get("id", {}).get("videoId")
        if not video_id or CHANNEL_NAME.lower() not in title.lower():
            continue
        if is_excluded_channel(snippet.get("channelId")):
            continue
        if re.search(r"#shorts?\b|\bshorts\b", title.lower()):
            print(f"  ⏭️ Explicit Short filtered: {title[:50]}")
            continue
        matches[video_id] = item

    if not matches:
        print("ℹ️ No qualifying live streams returned by search.")
        return []

    videos_response = youtube.videos().list(
        part="snippet,liveStreamingDetails",
        id=",".join(matches)
    ).execute()

    streams = []
    for detail in videos_response.get("items", []):
        video_id = detail["id"]
        snippet = detail.get("snippet", {})
        if is_excluded_channel(snippet.get("channelId")):
            continue
        broadcast = snippet.get("liveBroadcastContent", "none")
        live = detail.get("liveStreamingDetails", {})
        # The search request is for active broadcasts; reject videos that have ended
        # or are still merely scheduled according to their detailed metadata.
        is_active = (broadcast == "live" or bool(live.get("actualStartTime"))) \
            and not live.get("actualEndTime") and broadcast != "upcoming"
        if not is_active:
            print(f"  ⏭️ Not confirmed live: {video_id} (broadcast={broadcast})")
            continue
        original = matches[video_id]["snippet"]
        streams.append({
            "title": snippet.get("title", original.get("title", "")),
            "video_id": video_id,
            "channel_id": snippet.get("channelId") or original.get("channelId"),
            "channel": snippet.get("channelTitle", original.get("channelTitle", "")),
            "published_at": live.get("actualStartTime") or original["publishedAt"],
            "url": f"https://youtu.be/{video_id}",
            "thumbnail": select_thumbnail(
                snippet.get("thumbnails"), video_id,
                fallback=select_thumbnail(original.get("thumbnails"), video_id)),
            "live_broadcast_content": "live"
        })

    print(f"✅ {len(streams)} genuinely active streams confirmed")
    return streams

def discord_timestamp(iso):
    """Dynamic Discord local-time and relative timestamp for embeds."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    stamp = int(dt.timestamp())
    return f"<t:{stamp}:t> (<t:{stamp}:R>)"


def send_to_discord(streams, run_start=None, on_success=None):
    if DRY_RUN:
        print(f"🧪 DRY RUN: would announce {len(streams)} live streams; webhook disabled.")
        return []
    # Enforce the exclusion again for direct calls and FORCE_SEND_ALL.
    streams = [s for s in streams if not is_excluded_channel(s.get("channel_id"))]
    if not streams:
        print("⚠️ No live streams to send.")
        return []

    # Never turn a valid live alert into a text-only notification.
    require_thumbnails(streams)
    delivered = []

    print(f"\n📨 Sending {len(streams)} live stream(s) to Discord...\n")

    for stream in streams:
        age = humanize_ago(stream['published_at'], run_start)
        posted_value = discord_timestamp(stream['published_at'])

        title = f"🔴 LIVE: {stream['title']}"

        embed = {
            "title": title,
            "url": stream['url'],
            "color": 0xe53935,  # red for live
            "image": {"url": stream['thumbnail']},
            "fields": [
                {"name": "Channel", "value": stream['channel'], "inline": True},
                {"name": "Started", "value": posted_value, "inline": True},
            ],
            "timestamp": stream['published_at']
        }

        data = {
            "username": "Missioned Souls Reactions",
            "embeds": [embed]
        }

        if post_webhook(DISCORD_WEBHOOK_URL, data):
            print(f"✅ Sent: [{posted_value}] {stream['title'][:60]}...")
            delivered.append(stream)
            if on_success is not None:
                on_success(stream)
        else:
            print("::warning::Discord live alert delivery failed; unsent ID remains eligible for retry.")
            break
        time.sleep(1.3)

    return delivered


# ===================== MAIN =====================
if __name__ == "__main__":
    print("🚀 Missioned Souls Live Stream Checker Started\n")

    run_start = datetime.now(timezone.utc)
    print(f"⏰ Run started at: {run_start.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    sent_ids, last_check = load_state()
    print(f"📅 Last check: {last_check[:19] if last_check else 'None (first run)'}")
    print(f"📋 Previously sent IDs: {len(sent_ids)}\n")

    streams = find_active_live_streams()

    if last_check == "BOOTSTRAP":
        sent_ids = list(dict.fromkeys(s["video_id"] for s in streams))
        save_state(sent_ids)
        print("📌 Live bootstrap: current stream IDs seeded with no Discord posts.")
        raise SystemExit(0)

    if not streams:
        print("ℹ️ No active Missioned Souls live streams found.")
        # Still save state to update last_check
        save_state(sent_ids)
        print("\n🎉 All done!")
        raise SystemExit(0)

    if FORCE_SEND_ALL:
        new_streams = streams
        print(f"🔄 Force mode: Sending all {len(new_streams)} streams")
    else:
        # Filter by video ID, not timestamp
        new_streams = [s for s in streams if s['video_id'] not in sent_ids]
        print(f"🆕 {len(new_streams)} new live streams (not previously sent)")

    if DRY_RUN:
        for stream in new_streams[:MAX_TO_SEND]:
            print(f"🧪 WOULD ANNOUNCE LIVE: {stream['video_id']} {stream['title'][:70]}")
        print("🧪 DRY RUN: no Discord messages, no bookmark writes.")
        raise SystemExit(0)

    if new_streams:
        new_streams.sort(key=lambda x: x['published_at'], reverse=False)
        top_streams = new_streams[:MAX_TO_SEND]  # Drain oldest eligible alerts first.

        def confirmed(stream):
            if stream["video_id"] not in sent_ids:
                sent_ids.append(stream["video_id"])
            save_state(sent_ids)
            save_durable_ids("live", sent_ids)

        delivered = send_to_discord(top_streams, run_start=run_start, on_success=confirmed)
        # Also update last_check if nothing was successfully delivered.
        if not delivered:
            save_state(sent_ids)
        if len(delivered) < len(top_streams):
            raise RuntimeError("Some live alerts failed. Sent IDs persisted for retry.")
        print(f"📌 {len(sent_ids)} total IDs tracked")
    else:
        print("ℹ️ No new live streams – state unchanged")
        # Still update last_check
        save_state(sent_ids)

    print("\n🎉 All done!")
