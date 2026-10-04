import os
import time
import json
import re
import requests
from googleapiclient.discovery import build

# ================== READ CONFIG FROM ENVIRONMENT ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND", "10"))
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", "last_run.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

# Channels containing any of these keywords (case-insensitive) are skipped
EXCLUDED_CHANNEL_KEYWORDS = ["vlog"]

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


def is_excluded_channel(video):
    """Return True if the channel name contains any excluded keyword."""
    channel_lower = video.get('channel', '').lower()
    return any(kw in channel_lower for kw in EXCLUDED_CHANNEL_KEYWORDS)


def is_short_or_too_short(video):
    title_lower = video.get('title', '').lower()

    # Always filter explicit #shorts
    if '#shorts' in title_lower:
        return True

    # Never filter live or upcoming streams
    if video.get('live_broadcast_content') in ('live', 'upcoming'):
        return False

    # Never filter videos with unknown duration (0s) — could be live VODs,
    # premieres, or items YouTube hasn't populated duration for yet
    duration = video.get('duration_sec', 0)
    if duration == 0:
        return False

    # Only filter confirmed short videos
    return duration < 120


def get_reactions_with_stats():
    print(f"🔍 Searching latest reactions for {CHANNEL_NAME}...\n")

    search_response = youtube.search().list(
        part="snippet",
        q=f'"{CHANNEL_NAME}" (reacts OR reaction OR "first time" OR "react to" OR reacting)',
        type="video",
        maxResults=50,
        order="date"
    ).execute()

    items = search_response.get('items', [])
    if not items:
        print("⚠️ No search results returned.")
        return []

    video_ids = []
    temp_videos = []

    for item in items:
        video_id = item['id']['videoId']
        video = {
            'title': item['snippet']['title'],
            'video_id': video_id,
            'channel': item['snippet']['channelTitle'],
            'published_at': item['snippet']['publishedAt'],
            'url': f"https://youtu.be/{video_id}",
            'thumbnail': item['snippet'].get('thumbnails', {}).get('medium', {}).get('url'),
            'live_broadcast_content': item['snippet'].get('liveBroadcastContent', 'none')
        }
        temp_videos.append(video)
        video_ids.append(video_id)

    # Stats + duration in one batch call
    stats_dict = {}
    duration_dict = {}
    if video_ids:
        stats_response = youtube.videos().list(
            part="statistics,contentDetails",
            id=",".join(video_ids)
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

        # Skip excluded channels entirely
        if is_excluded_channel(video):
            continue

        if not is_short_or_too_short(video):
            all_videos.append(video)

    # Sort ascending (oldest first)
    all_videos.sort(key=lambda x: x['published_at'], reverse=False)

    for video in temp_videos:
        views = video.get('view_count', 0)
        if is_excluded_channel(video):
            status = "🚫 (vlog channel)"
        elif is_short_or_too_short(video):
            status = "⏭️ (filtered)"
        elif video.get('live_broadcast_content') == 'live':
            status = "🔴 (live)"
        elif video.get('live_broadcast_content') == 'upcoming':
            status = "🕒 (upcoming)"
        else:
            status = "✅"
        print(f"{views:8,} views | {video['channel'][:20]:20} | {video['title'][:55]} {status}")

    print(f"\n✅ Found {len(all_videos)} reactions (after filtering).")
    return all_videos


def send_to_discord(videos, max_to_send=5):
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

        embed = {
            "title": title,
            "url": video['url'],
            "color": 0xe53935 if video.get('live_broadcast_content') == 'live' else 0x1e88e5,
            "image": {"url": video.get('thumbnail')} if video.get('thumbnail') else None,
            "fields": [
                {"name": "Channel", "value": video['channel'], "inline": True},
                {"name": "Views", "value": f"{video.get('view_count', 0):,}", "inline": True},
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
                print(f"✅ Sent: {title[:60]}...")
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

    videos = get_reactions_with_stats()

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
        send_to_discord(top_videos, max_to_send=MAX_TO_SEND)

        newest_timestamp = new_videos[-1]['published_at']
        save_last_run(newest_timestamp)
        print(f"📌 Bookmark updated to: {newest_timestamp}")
    else:
        send_to_discord([], max_to_send=MAX_TO_SEND)
        print("ℹ️ No new videos – bookmark unchanged")

    print("\n🎉 All done!")
