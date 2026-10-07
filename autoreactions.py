import os
import time
import json
import re
import requests
from datetime import datetime, timezone
from googleapiclient.discovery import build

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

youtube = build('youtube', 'v3', developerKey=API_KEY)


def load_last_run():
    try:
        with open(LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get('last_published_at')
    except:
        return None


def save_last_run(published_at):
    with open(LAST_RUN_FILE, 'w', encoding='utf-8') as f:
        json.dump({"last_published_at": published_at}, f, indent=2)
    print(f"💾 Updated {LAST_RUN_FILE} → {published_at[:10]}")


def load_tracked_channels():
    try:
        with open(TRACKED_CHANNELS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('channels', {})
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

    # 1. Filter explicit #short / #shorts hashtags (highest priority)
    if '#short' in title_lower or 'shorts' in title_lower:
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

    if '#short' in title_lower:
        return "has #short"
    if 'shorts' in title_lower:
        return "has 'shorts'"

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

            # Capture liveStreamingDetails (present for active, upcoming, and archived streams)
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


def send_to_discord(videos, max_to_send=5, run_start=None):
    if not videos:
        print("⚠️ No new videos to send.")
        return

    print(f"\n📨 Sending {min(max_to_send, len(videos))} new reactions to Discord...\n")

    for video in videos[:max_to_send]:
        title = video['title']
        if video.get('live_broadcast_content') == 'live':
            title = f"🔴 LIVE: {title}"
        elif video.get('live_broadcast_content') == 'upcoming':
            title = f"🕒 UPCOMING: {title}"

        age = video.get('age_at_run', humanize_ago(video['published_at'], run_start))

        embed = {
            "title": title,
            "url": video['url'],
            "color": 0xe53935 if video.get('live_broadcast_content') == 'live' else 0x1e88e5,
            "image": {"url": video.get('thumbnail')} if video.get('thumbnail') else None,
            "fields": [
                {"name": "Channel", "value": video['channel'], "inline": True},
                {"name": "Views", "value": f"{video.get('view_count', 0):,}", "inline": True},
                {"name": "Posted", "value": age, "inline": True},
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
            if response.status_code == 204:
                print(f"✅ Sent: [{age}] {title[:60]}...")
            else:
                print(f"❌ Discord error {response.status_code}")
        except Exception as e:
            print(f"❌ Failed to send: {e}")
        time.sleep(1.3)


# ===================== MAIN =====================
if __name__ == "__main__":
    print("🚀 Missioned Souls Reaction Tracker Started\n")

    last_published = load_last_run()
    print(f"📅 Last run timestamp: {last_published[:10] if last_published else 'No bookmark — will be set this run'}")

    videos, run_start = get_reactions_with_stats()

    if not last_published:
        if videos:
            newest_timestamp = videos[-1]['published_at']
            save_last_run(newest_timestamp)
            print(f"📌 Initial bookmark set to: {newest_timestamp}")
        else:
            print("ℹ️ No videos found — bookmark not set")
        print("\n🎉 All done!")
        raise SystemExit(0)

    if FORCE_SEND_ALL:
        new_videos = videos
        print(f"🔄 Force mode: Sending latest {len(new_videos)} videos")
    else:
        new_videos = [v for v in videos if v['published_at'] > last_published]
        print(f"🆕 Found {len(new_videos)} new reactions since last run")

    new_videos.sort(key=lambda x: x['published_at'], reverse=False)

    if new_videos:
        top_videos = new_videos[-MAX_TO_SEND:] if len(new_videos) >= MAX_TO_SEND else new_videos
        send_to_discord(top_videos, max_to_send=MAX_TO_SEND, run_start=run_start)

        newest_timestamp = new_videos[-1]['published_at']
        save_last_run(newest_timestamp)
        print(f"📌 Bookmark updated to: {newest_timestamp}")
    else:
        send_to_discord([], max_to_send=MAX_TO_SEND)
        print("ℹ️ No new videos – bookmark unchanged")

    print("\n🎉 All done!")
