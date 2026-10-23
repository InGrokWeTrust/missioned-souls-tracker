import os
import time
import json
import re
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from notification_history import read_ids, save_ids
from channel_exclusions import is_excluded_channel

# ================== READ CONFIG FROM ENVIRONMENT ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND", "10"))
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "5"))
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", "last_run.json")
TRACKED_CHANNELS_FILE = os.environ.get("TRACKED_CHANNELS_FILE", "tracked_channels.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

# ===========================================

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")

youtube = build('youtube', 'v3', developerKey=API_KEY)


def load_last_run():
    """Read the legacy timestamp bookmark; refuse corrupt state."""
    try:
        with open(LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except FileNotFoundError:
        return None
    if not isinstance(state, dict):
        raise ValueError("Invalid last-run bookmark (expected object)")
    value = state.get('last_published_at')
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError("Invalid last_published_at in bookmark")
    return value


def save_last_run(published_at):
    """Atomically write the original bookmark only after confirmed delivery."""
    temporary = LAST_RUN_FILE + ".tmp"
    try:
        with open(temporary, 'w', encoding='utf-8') as f:
            json.dump({"last_published_at": published_at}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, LAST_RUN_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"💾 Updated {LAST_RUN_FILE} → {published_at[:10]}")


def load_tracked_channels():
    try:
        with open(TRACKED_CHANNELS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            channels = data.get('channels', {})
            # Defense in depth: even an older tracked_channels.json cannot
            # permit an excluded uploader to reach Discord.
            return {cid: info for cid, info in channels.items()
                    if not is_excluded_channel(cid)}
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


def is_missioned_souls_reaction(title):
    """Return True if the title mentions Missioned Souls (case-insensitive)."""
    return CHANNEL_NAME.lower() in title.lower()


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
        return []

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
        print("⚠️ No tracked channels found. Run discover_channels.py first.")
        return [], run_start

    print(f"📋 Tracking {len(tracked)} channels\n")

    # Stage 1: fetch all candidate videos
    candidates = []
    no_ms_count = 0
    silent_channels = []

    for channel_id, info in tracked.items():
        channel_title = info.get('title', channel_id) if isinstance(info, dict) else str(info)

        videos = fetch_channel_videos(channel_id, channel_title)
        reaction_videos = [v for v in videos if is_missioned_souls_reaction(v['title'])]
        no_ms_count += len(videos) - len(reaction_videos)

        for v in reaction_videos:
            candidates.append(v)

        if not reaction_videos:
            silent_channels.append(channel_title)

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

    if not candidates:
        print("⚠️ No Missioned Souls reactions found in tracked channels.")
        return [], run_start

    # Stage 2: batch fetch stats + duration + liveStreamingDetails
    video_ids = [v['video_id'] for v in candidates]
    stats_dict = {}
    duration_dict = {}
    live_details_dict = {}

    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        stats_response = youtube.videos().list(
            part="statistics,contentDetails,liveStreamingDetails",
            id=",".join(chunk)
        ).execute()

        for item in stats_response.get('items', []):
            vid_id = item['id']
            stats = item.get('statistics', {})
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
        video['live_streaming_details'] = live_details_dict.get(vid_id)
        video['age_at_run'] = humanize_ago(video['published_at'], run_start)

        if is_short_or_too_short(video):
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

    return all_videos, run_start


def send_to_discord(videos, max_to_send=5, run_start=None, seen_ids=None):
    """Send and durably record each accepted webhook, before the next send."""
    # Final protection even if callers bypass the normal channel scan.
    videos = [v for v in videos if not is_excluded_channel(v.get('channel_id'))]
    if not videos:
        print("⚠️ No new videos to send.")
        return []
    if seen_ids is None:
        seen_ids = set()
    delivered = []

    print(f"\n📨 Sending {min(max_to_send, len(videos))} new reactions to Discord...\n")

    for video in videos[:max_to_send]:
        vid = video['video_id']
        if vid in seen_ids:
            print(f"⏭️ Already delivered: {vid}")
            continue
        title = video['title']
        if video.get('live_broadcast_content') == 'live':
            title = f"🔴 LIVE: {title}"
        elif video.get('live_broadcast_content') == 'upcoming':
            title = f"🕒 UPCOMING: {title}"

        age = video.get('age_at_run', humanize_ago(video['published_at'], run_start))
        published_str = format_published_time(video['published_at'])
        posted_value = f"{published_str} ({age})" if published_str else age

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

        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
        except Exception as e:
            raise RuntimeError(f"Discord delivery failed for {vid}; bookmark unchanged") from e
        if response.status_code != 204:
            raise RuntimeError(f"Discord HTTP {response.status_code} for {vid}; bookmark unchanged")
        # Durable history is the primary dedup source. If it cannot be written,
        # stop immediately: never mark this notification as fully acknowledged.
        save_ids("reactions", [vid])
        seen_ids.add(vid)
        delivered.append(video)
        print(f"✅ Sent: [{posted_value}] {title[:60]}...")
        time.sleep(1.3)
    return delivered


# ===================== MAIN =====================
def run():
    print("🚀 Missioned Souls Reaction Tracker Started\n")
    # Read both confirmed-delivery ledgers *before* any YouTube or Discord call.
    # An unavailable ledger causes a safe failure rather than duplicate posts.
    reaction_ids = read_ids("reactions")
    live_ids = read_ids("live")
    already_delivered = reaction_ids | live_ids
    print(f"📋 Durable delivered history: {len(reaction_ids)} reactions, "
          f"{len(live_ids)} live IDs")

    last_published = load_last_run()
    print(f"📅 Last run timestamp: {last_published[:10] if last_published else 'No bookmark — will be set this run'}")
    videos, run_start = get_reactions_with_stats()
    videos = [v for v in videos if not is_excluded_channel(v.get('channel_id'))]

    if not last_published:
        # Missing legacy bookmark: seed the cutoff without replaying uploads.
        if videos:
            newest_timestamp = max(v['published_at'] for v in videos)
            save_last_run(newest_timestamp)
            print(f"📌 Initial bookmark set without Discord delivery: {newest_timestamp}")
        else:
            print("ℹ️ No videos found — bookmark not set")
        print("\n🎉 All done!")
        return []

    unseen = []
    seen_this_run = set()
    for v in videos:
        vid = v["video_id"]
        if vid in already_delivered or vid in seen_this_run:
            continue
        seen_this_run.add(vid)
        if FORCE_SEND_ALL or v["published_at"] > last_published:
            unseen.append(v)
    unseen.sort(key=lambda v: (v["published_at"], v["video_id"]))
    print(f"🆕 {len(unseen)} eligible unseen reaction videos "
          f"(previously delivered IDs always excluded)")

    # Send oldest eligible videos first, so a capped batch or webhook failure
    # cannot push the timestamp past still-undelivered videos.
    batch = unseen[:MAX_TO_SEND]
    if not batch:
        print("ℹ️ No undelivered reactions — bookmark unchanged")
        return []
    delivered = send_to_discord(
        batch, max_to_send=MAX_TO_SEND, run_start=run_start,
        seen_ids=already_delivered)
    if delivered:
        newest_timestamp = max(v["published_at"] for v in delivered)
        if newest_timestamp > last_published:
            save_last_run(newest_timestamp)
        print(f"📌 {len(delivered)} newly sent IDs saved durably")
    print("\n🎉 All done!")
    return delivered


if __name__ == "__main__":
    run()
