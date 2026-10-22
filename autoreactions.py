import os
import time
import json
import re
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from webhook_delivery import post_webhook
from channel_exclusions import is_excluded_channel
from reaction_channel_policy import is_official_artist_channel
from verified_nitro_reactions import is_verified_nitro_reaction
from video_release_policy import is_published_vod
from notification_history import save_ids as save_durable_ids
from reaction_state import (StateError, load_state, save_state, plan_notifications,
                            acknowledge, read_live_sent_ids, reconcile_live_notifications)

# ================== READ CONFIG FROM ENVIRONMENT ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = (os.environ.get("CHANNEL_NAME") or "Missioned Souls")
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND") or "10")
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL") or "5")
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
LAST_RUN_FILE = (os.environ.get("LAST_RUN_FILE") or "last_run.json")
LIVE_LAST_RUN_FILE = (os.environ.get("LIVE_LAST_RUN_FILE") or "live_last_run.json")
TRACKED_CHANNELS_FILE = (os.environ.get("TRACKED_CHANNELS_FILE") or "tracked_channels.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL and not DRY_RUN:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

# ===========================================

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")

youtube = build('youtube', 'v3', developerKey=API_KEY)


def load_tracked_channels():
    try:
        with open(TRACKED_CHANNELS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            channels = data.get('channels', {})
            return {cid: info for cid, info in channels.items()
                    if not is_excluded_channel(cid) and not is_official_artist_channel(cid)}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def parse_duration(duration_str):
    if not duration_str:
        return 0
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def is_missioned_souls_reaction(title, *, video_id=None, channel_id=None):
    """Preserve existing title matching; add only human-verified Nitro exceptions.

    An unknown Nitro title is never promoted just because it uses that name.
    Exact video and uploading channel IDs must match the verified allowlist.
    """
    if is_official_artist_channel(channel_id):
        return False
    if "paper dove" in title.lower() and "#missionedsouls" in title.lower():
        return False
    if CHANNEL_NAME.lower() in title.lower():
        return True
    return is_verified_nitro_reaction({
        "title": title, "video_id": video_id, "channel_id": channel_id
    })


def is_archived_livestream(video):
    """Return True if the video has livestream details (active, upcoming, or archived)."""
    return video.get('live_streaming_details') is not None


def is_short_or_too_short(video):
    title_lower = video.get('title', '').lower()

    # 1. Prefer explicit Shorts labels; generic words in unrelated titles should not match.
    if re.search(r"#shorts?\b|\bshorts\b", title_lower):
        return True

    # 2. Keep ALL livestreams — active, upcoming, or archived
    if is_archived_livestream(video):
        return False

    # 3. Keep live/upcoming detected via snippet liveBroadcastContent
    if video.get('live_broadcast_content') in ('live', 'upcoming'):
        return False

    duration = video.get('duration_sec', 0)

    # 4. Keep videos with unknown duration (safer than dropping them)
    if duration == 0:
        return False

    # 5. Filter confirmed short videos
    return duration < 120


def classify_filter_reason(video):
    """Return a short reason string for why a video was filtered."""
    title_lower = video.get('title', '').lower()

    if re.search(r"#shorts?\b|\bshorts\b", title_lower):
        return "marked as Short"

    duration = video.get('duration_sec', 0)
    if duration > 0 and duration < 120:
        return f"too short ({duration}s)"

    return "unknown"


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
    """Return the absolute publish time in Pacific/Auckland timezone (e.g. '6:18 PM')."""
    try:
        ts = published_at_iso.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Convert to Pacific/Auckland
        local_dt = dt.astimezone(AUCKLAND_TZ)
        return local_dt.strftime('%I:%M %p %Z').lstrip('0')
    except Exception:
        return ""


def fetch_channel_videos(channel_id, channel_title):
    """Fetch the newest N videos from a channel's uploads playlist."""
    uploads_playlist_id = "UU" + channel_id[2:]

    try:
        response = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=MAX_VIDEOS_PER_CHANNEL
        ).execute()
    except Exception as e:
        print(f"  ❌ Error fetching {channel_title}: {e}")
        return None

    videos = []
    for item in response.get('items', []):
        snippet = item['snippet']
        content = item.get('contentDetails', {})
        video_id = content.get('videoId') or snippet.get('resourceId', {}).get('videoId')
        if not video_id:
            continue

        videos.append({
            'title': snippet['title'],
            'video_id': video_id,
            'channel': channel_title,
            'channel_id': channel_id,
            'published_at': content.get('videoPublishedAt') or snippet.get('publishedAt'),
            'url': f"https://youtu.be/{video_id}",
            'thumbnail': snippet.get('thumbnails', {}).get('medium', {}).get('url'),
            'live_broadcast_content': snippet.get('liveBroadcastContent', 'none'),
            'live_streaming_details': None  # filled in later
        })

    return videos


def get_reactions_with_stats():
    run_start = datetime.now(timezone.utc)
    print(f"🔍 Checking {CHANNEL_NAME} reactions from tracked channels...")
    print(f"⏰ Run started at: {run_start.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    tracked = load_tracked_channels()
    if not tracked:
        raise RuntimeError("No tracked channels available: refusing to seed an empty scan.")

    print(f"📋 Tracking {len(tracked)} channels\n")

    # Stage 1: fetch all candidate videos
    candidates = []
    scanned_channel_ids = set()
    failed_channels = []
    no_ms_count = 0
    silent_channels = []

    for channel_id, info in tracked.items():
        if is_excluded_channel(channel_id):
            continue
        channel_title = info.get('title', channel_id) if isinstance(info, dict) else str(info)

        videos = fetch_channel_videos(channel_id, channel_title)
        if videos is None:
            failed_channels.append(channel_title)
            continue
        scanned_channel_ids.add(channel_id)
        reaction_videos = [v for v in videos if is_missioned_souls_reaction(
            v["title"], video_id=v["video_id"], channel_id=v["channel_id"]
        )]
        no_ms_count += len(videos) - len(reaction_videos)

        for v in reaction_videos:
            candidates.append(v)

        if not reaction_videos:
            silent_channels.append(channel_title)

    if failed_channels:
        print(f"::warning::{len(failed_channels)} channels failed to scan and will be retried next run.")
    if silent_channels:
        print(f"⏭️  Channels with no MS reactions in last {MAX_VIDEOS_PER_CHANNEL}:")
        for ch in silent_channels:
            print(f"     • {ch}")
        print()

    print(f"📊 Stage 1 — Channel scan:")
    print(f"   Channels checked         : {len(tracked)}")
    print(f"   Videos scanned           : {len(candidates) + no_ms_count}")
    print(f"   MS keyword matches       : {len(candidates)}")
    print(f"   No MS mention (skipped)  : {no_ms_count}\n")

    if not scanned_channel_ids:
        raise RuntimeError("All channel scans failed; state will remain untouched.")
    if not candidates:
        print("⚠️ No Missioned Souls reactions found in tracked channels.")
        return [], run_start, scanned_channel_ids

    # Stage 2: batch fetch stats + duration + liveStreamingDetails
    video_ids = [v['video_id'] for v in candidates]
    stats_dict = {}
    duration_dict = {}
    live_details_dict = {}
    descriptions_dict = {}
    broadcast_status_dict = {}
    confirmed_ids = set()

    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        stats_response = youtube.videos().list(
            part="statistics,contentDetails,liveStreamingDetails,snippet",
            id=",".join(chunk)
        ).execute()

        for item in stats_response.get('items', []):
            vid_id = item['id']
            confirmed_ids.add(vid_id)
            descriptions_dict[vid_id] = item.get('snippet', {}).get('description', '')
            stats = item.get('statistics', {})
            broadcast_status_dict[vid_id] = item.get('snippet', {}).get('liveBroadcastContent', 'none')
            stats_dict[vid_id] = {
                'view_count': int(stats.get('viewCount', 0)),
                'like_count': int(stats.get('likeCount', 0)),
                'comment_count': int(stats.get('commentCount', 0))
            }
            content_details = item.get('contentDetails', {})
            duration_str = content_details.get('duration', 'PT0S')
            duration_dict[vid_id] = parse_duration(duration_str)

            if 'liveStreamingDetails' in item:
                live_details_dict[vid_id] = item['liveStreamingDetails']

    # Stage 3: apply filters, log per-video outcome
    all_videos = []
    filtered_log = []

    for video in candidates:
        vid_id = video['video_id']
        vid_stats = stats_dict.get(vid_id, {})
        video['view_count'] = vid_stats.get('view_count', 0)
        video['like_count'] = vid_stats.get('like_count', 0)
        video['comment_count'] = vid_stats.get('comment_count', 0)
        video['duration_sec'] = duration_dict.get(vid_id, 0)
        video['metadata_confirmed'] = vid_id in confirmed_ids
        video['live_streaming_details'] = live_details_dict.get(vid_id)
        video['description'] = descriptions_dict.get(vid_id, '')
        video['live_broadcast_content'] = broadcast_status_dict.get(
            vid_id, video.get('live_broadcast_content', 'none'))
        video['age_at_run'] = humanize_ago(video['published_at'], run_start)

        if not is_published_vod(video):
            filtered_log.append((video, "upcoming, live, or unconfirmed VOD"))
        elif is_short_or_too_short(video):
            reason = classify_filter_reason(video)
            filtered_log.append((video, reason))
        else:
            all_videos.append(video)

    if filtered_log:
        print(f"🚫 Stage 2 — Filtered out ({len(filtered_log)} videos):")
        for v, reason in filtered_log:
            ch = v['channel'][:24]
            print(f"   ⏭️  [{reason}] {ch:24} | {v['title'][:55]}")
        print()

    all_videos.sort(key=lambda x: x['published_at'], reverse=False)

    print(f"📊 Stage 3 — Final:")
    print(f"   Candidates               : {len(candidates)}")
    print(f"   Filtered (shorts/other)  : {len(filtered_log)}")
    print(f"   ✅ Kept for posting      : {len(all_videos)}\n")

    return all_videos, run_start, scanned_channel_ids



def reconfirm_older_pending_vods(pending, scanned_ids, *, now=None, limit=50):
    """Safely recheck queued videos no longer covered by the newest-upload scan.

    Only a bounded number of older items are checked in each run (one batched
    YouTube videos.list request), rotating the selection every 30-minute slot.
    An unavailable, upcoming, live, private, changed-channel, or short video
    remains pending. A failed API request fails closed: no messages are sent.
    """
    older = [v for v in pending if v["video_id"] not in scanned_ids]
    if not older:
        return set()
    if not isinstance(limit, int) or limit < 1 or limit > 50:
        raise ValueError("Pending VOD recheck limit must be between 1 and 50")
    now = now or datetime.now(timezone.utc)
    if len(older) > limit:
        # Offset changes per half-hour so an old upcoming video cannot starve
        # the rest of the queue. No cursor or bookmark schema change required.
        slot = int(now.timestamp()) // 1800
        start = (slot * limit) % len(older)
        selected = (older[start:] + older[:start])[:limit]
    else:
        selected = older
    queued = {v["video_id"]: v for v in selected}
    print(f"🔎 Rechecking {len(queued)} older queued VODs "
          f"({len(older)} not covered by the latest upload scan).")
    try:
        response = youtube.videos().list(
            part="snippet,contentDetails,liveStreamingDetails,status",
            id=",".join(queued)
        ).execute()
    except Exception as exc:
        raise StateError("Unable to reconfirm older queued VODs; queue preserved, "
                         "refusing Discord delivery.") from exc
    ready = set()
    for item in response.get("items", []):
        vid = item.get("id")
        queued_video = queued.get(vid)
        if queued_video is None:
            continue
        snippet = item.get("snippet") or {}
        current_title = snippet.get("title", "")
        if (not isinstance(current_title, str)
                or snippet.get("channelId") != queued_video["channel_id"]
                or is_excluded_channel(snippet.get("channelId"))
                or not is_missioned_souls_reaction(
                    current_title, video_id=vid, channel_id=snippet.get("channelId"))):
            continue
        privacy = (item.get("status") or {}).get("privacyStatus")
        if privacy is not None and privacy != "public":
            continue
        fresh = {
            "title": current_title,
            "metadata_confirmed": True,
            "live_broadcast_content": snippet.get("liveBroadcastContent"),
            "live_streaming_details": item.get("liveStreamingDetails"),
            "duration_sec": parse_duration(
                (item.get("contentDetails") or {}).get("duration"))
        }
        if is_published_vod(fresh) and not is_short_or_too_short(fresh):
            ready.add(vid)
    print(f"✅ {len(ready)} older queued VODs confirmed published; "
          f"others remain pending.")
    return ready


def discord_timestamp(iso):
    """Dynamic Discord local-time and relative timestamp for embeds."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    stamp = int(dt.timestamp())
    return f"<t:{stamp}:t> (<t:{stamp}:R>)"


def send_to_discord(videos, max_to_send=5, run_start=None, on_success=None):
    if DRY_RUN:
        print(f"🧪 DRY RUN: would deliver up to {min(max_to_send, len(videos))} videos; webhook disabled.")
        return []
    # Last line of defence, even for FORCE_SEND_ALL or direct callers.
    videos = [v for v in videos if not is_excluded_channel(v.get("channel_id"))
              and not is_official_artist_channel(v.get("channel_id"))]
    if not videos:
        print("⚠️ No new videos to send.")
        return []

    delivered = []

    print(f"\n📨 Sending {min(max_to_send, len(videos))} new reactions to Discord...\n")

    for video in videos[:max_to_send]:
        title = video['title']
        if video.get('live_broadcast_content') == 'live':
            title = f"🔴 LIVE: {title}"
        elif video.get('live_broadcast_content') == 'upcoming':
            title = f"🕒 UPCOMING: {title}"

        age = video.get('age_at_run', humanize_ago(video['published_at'], run_start))
        posted_value = discord_timestamp(video['published_at'])

        embed = {
            "title": title,
            "url": video['url'],
            "color": 0xe53935 if video.get('live_broadcast_content') == 'live' else 0x1e88e5,
            "image": {"url": video.get('thumbnail')} if video.get('thumbnail') else None,
            "fields": [
                {"name": "Channel", "value": video['channel'], "inline": True},
                {"name": "Views", "value": f"{video.get('view_count', 0):,}", "inline": True},
                {"name": "Posted", "value": posted_value, "inline": True},
                {"name": "Likes", "value": f"{video.get('like_count', 0):,}", "inline": True},
                {"name": "Comment", "value": f"{video.get('comment_count', 0):,}", "inline": True},
            ],
            "timestamp": video['published_at']
        }

        data = {
            "username": "Missioned Souls Reactions",
            "embeds": [embed]
        }

        if post_webhook(DISCORD_WEBHOOK_URL, data):
            print(f"✅ Sent: [{posted_value}] {title[:60]}...")
            delivered.append(video)
            if on_success is not None:
                on_success(video)
        else:
            print("::warning::Discord delivery failed; stopping to preserve the unsent bookmark.")
            break
        time.sleep(1.3)

    return delivered


def run_tracker(videos, tracked_channel_ids, *, force_send_all=False, run_start=None,
                channel_added_at=None):
    """Persist the queue BEFORE delivery and after each confirmed notification."""
    allow_bootstrap = os.environ.get("BOOTSTRAP_STATE_IF_MISSING", "false").lower() == "true"
    state = load_state(LAST_RUN_FILE, allow_bootstrap=allow_bootstrap)
    # A previously queued HCJB notification must never survive deployment
    # into a future delivery. Remove only blocked-channel pending entries;
    # preserve all other queue items and already-seen history intact.
    blocked_pending = sum(is_excluded_channel(v.get("channel_id")) or
                          is_official_artist_channel(v.get("channel_id")) for v in state["pending"])
    if blocked_pending:
        state["pending"] = [v for v in state["pending"]
                            if not is_excluded_channel(v.get("channel_id"))
                            and not is_official_artist_channel(v.get("channel_id"))]
        print(f"🚫 Removed {blocked_pending} excluded-channel pending Discord alerts.")
    videos = [v for v in videos if not is_excluded_channel(v.get("channel_id"))
              and not is_official_artist_channel(v.get("channel_id"))]
    tracked_channel_ids = {cid for cid in tracked_channel_ids
                           if not is_excluded_channel(cid)
                           and not is_official_artist_channel(cid)}
    if channel_added_at is not None:
        channel_added_at = {cid: date for cid, date in channel_added_at.items()
                            if not is_excluded_channel(cid)
                            and not is_official_artist_channel(cid)}
    bootstrap = state.get("_bootstrap", False)
    if bootstrap and not videos:
        raise StateError("Cannot bootstrap from an empty reaction scan.")
    live_ids = read_live_sent_ids(LIVE_LAST_RUN_FILE)
    # Restore only independently confirmed historical Discord deliveries, never inferred uploads.
    with open(os.path.join(os.path.dirname(__file__), 'historical_deliveries.json'), encoding='utf-8') as f:
        historical_ids = json.load(f)['confirmed_sent_video_ids']
    for vid in historical_ids:
        if not isinstance(vid, str) or not vid:
            raise StateError('Invalid historical delivered video ID')
        if vid not in state['seen_video_ids']:
            state['seen_video_ids'].append(vid)
    state['pending'] = [v for v in state['pending'] if v['video_id'] not in historical_ids]
    plan_notifications(state, videos, tracked_channel_ids, now=run_start,
                       channel_added_at=channel_added_at)
    # Only confirmed archived/livestream IDs are removed; ordinary reactions are unaffected.
    reconcile_live_notifications(state, live_ids)
    if not DRY_RUN:
        # Save the queue before any supplementary metadata fetch or delivery.
        save_state(LAST_RUN_FILE, state)
    ready_ids = {v['video_id'] for v in videos if is_published_vod(v)}
    if not bootstrap and not force_send_all:
        ready_ids.update(reconfirm_older_pending_vods(
            state["pending"], {v["video_id"] for v in videos}, now=run_start
        ))
    if DRY_RUN:
        if bootstrap:
            chosen = []
        elif force_send_all:
            chosen = [v for v in videos if v['video_id'] in ready_ids
                      and v['video_id'] not in state['seen_video_ids']
                      and v['video_id'] not in live_ids][:MAX_TO_SEND]
        else:
            chosen = [v for v in state['pending'] if v['video_id'] in ready_ids][:MAX_TO_SEND]
        print(f"🧪 DRY RUN: {len(chosen)} would be sent, {len(state['pending'])} pending in simulated state.")
        for v in chosen:
            print(f"  WOULD SEND: {v['video_id']} {v['title'][:70]}")
        print("🧪 DRY RUN: no Discord messages, no bookmark writes.")
        return []

    # A one-time explicit bootstrap never sends existing videos, even in force mode.
    if bootstrap:
        print("📌 Bootstrap complete: current uploads were marked seen; no posts sent.")
        return []

    if force_send_all:
        # Force mode must not override the publication-safety policy.
        chosen = sorted(
            [v for v in videos if is_published_vod(v)
             and v['video_id'] not in state['seen_video_ids']
             and v['video_id'] not in live_ids],
            key=lambda v: (v["published_at"], v["video_id"])
        )[-MAX_TO_SEND:]
        print(f"🔄 Force replay: {len(chosen)} recent videos; state remains unchanged.")
        return send_to_discord(chosen, max_to_send=MAX_TO_SEND, run_start=run_start)

    # Recent uploads and older queued items were independently reconfirmed above.
    chosen = [v for v in state['pending'] if v['video_id'] in ready_ids][:MAX_TO_SEND]
    if not chosen:
        print("ℹ️ No pending reactions to deliver.")
        return []

    def confirmed(video):
        acknowledge(state, video)
        save_state(LAST_RUN_FILE, state)
        save_durable_ids("reactions", state["seen_video_ids"])

    delivered = send_to_discord(
        chosen, max_to_send=MAX_TO_SEND, run_start=run_start, on_success=confirmed
    )
    print(f"📨 Delivered {len(delivered)}; {len(state['pending'])} pending remain.")
    if len(delivered) != len(chosen):
        raise RuntimeError("Discord delivery failed. State saved; pending notifications remain queued.")
    return delivered


# ===================== MAIN =====================
if __name__ == "__main__":
    print("🚀 Missioned Souls Reaction Tracker Started\n")
    # Fail before reaching YouTube or Discord if bookmark is missing or corrupt.
    load_state(LAST_RUN_FILE, allow_bootstrap=os.environ.get(
        "BOOTSTRAP_STATE_IF_MISSING", "false").lower() == "true")

    videos, run_start, scanned_ids = get_reactions_with_stats()
    channels = load_tracked_channels()
    channel_added_at = {
        cid: info.get('added_at') for cid, info in channels.items()
        if isinstance(info, dict) and info.get('added_at')
    }
    run_tracker(videos, scanned_ids, force_send_all=FORCE_SEND_ALL,
                run_start=run_start, channel_added_at=channel_added_at)
    print("\n🎉 All done!")
