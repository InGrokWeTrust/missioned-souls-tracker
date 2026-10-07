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


def is_short_or_too_short(video):
    title_lower = video.get('title', '').lower()

    if '#shorts' in title_lower:
        return True

    if video.get('live_broadcast_content') in ('live', 'upcoming'):
        return False

    duration = video.get('duration_sec', 0)
    if duration == 0:
        return False

    return duration < 120


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
    # Uploads playlist ID = channel ID with "UC" → "UU"
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
            'live_broadcast_content': snippet.get('liveBroadcastContent', 'none')
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

    temp_videos = []

    for channel_id, info in tracked.items():
        channel_title = info.get('title', channel_id) if isinstance(info, dict) else str(info)

        videos = fetch_channel_videos(channel_id, channel_title)

        # Only keep videos that mention Missioned Souls in the title
        reaction_videos = [v for v in videos if is_missioned_souls_reaction(v['title'])]

        for v in reaction_videos:
            temp_videos.append(v)

        if reaction_videos:
            for v in reaction_videos:
                print(f"  ✅ {channel_title[:25]:25} | {v['title'][:60]}")
        else:
            print(f"  ⏭️  {channel_title[:25]:25} | (no MS reactions in last {MAX_VIDEOS_PER_CHANNEL})")

    if not temp_videos:
        print("\n⚠️ No Missioned Souls reactions found in tracked channels.")
        return [], run_start

    # Batch fetch stats + duration for all candidate videos
    video_ids = [v['video_id'] for v in temp_videos]
    stats_dict = {}
    duration_dict = {}

    # API allows up to 50 IDs per call
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        stats_response = youtube.videos().list(
            part="statistics,contentDetails",
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

    all_videos = []
    for video in temp_videos:
        vid_id = video['video_id']
        vid_stats = stats_dict.get(vid_id, {})
        video['view_count'] = vid_stats.get('view_count', 0)
        video['like_count'] = vid_stats.get('like_count', 0)
        video['comment_count'] = vid_stats.get('comment_count', 0)
        video['duration_sec'] = duration_dict.get(vid_id, 0)
        video['age_at_run'] = humanize_ago(video['published_at'], run_start)

        if not is_short_or_too_short(video):
            all_videos.append(video)

    all_videos.sort(key=lambda x: x['published_at'], reverse=False)

    print(f"\n✅ Found {len(all_videos)} Missioned Souls reactions (after filtering).")
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
