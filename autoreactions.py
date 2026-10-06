import os
import time
import json
import re
import requests
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build

# ================== READ CONFIG FROM ENVIRONMENT ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND", "10"))
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", "last_run.json")
TRACKED_CHANNELS_FILE = os.environ.get("TRACKED_CHANNELS_FILE", "tracked_channels.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

EXCLUDED_CHANNEL_KEYWORDS = ["vlog"]
VIDEOS_PER_CHANNEL = 5
SAFETY_WINDOW_DAYS = 7
# ===========================================

youtube = build('youtube', 'v3', developerKey=API_KEY)


def load_last_run():
    try:
        with open(LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('last_published_at'), data.get('sent_video_ids', [])
    except (FileNotFoundError, json.JSONDecodeError):
        return None, []


def save_last_run(published_at, sent_video_ids):
    sent_video_ids = sent_video_ids[-500:]
    with open(LAST_RUN_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            "last_published_at": published_at,
            "sent_video_ids": sent_video_ids
        }, f, indent=2)
    print(f"💾 Updated {LAST_RUN_FILE} → {published_at[:10]} ({len(sent_video_ids)} IDs tracked)")


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


def is_excluded_channel(channel_name):
    channel_lower = (channel_name or '').lower()
    return any(kw in channel_lower for kw in EXCLUDED_CHANNEL_KEYWORDS)


def is_missioned_souls_video(video):
    title_lower = video.get('title', '').lower()
    return 'missioned souls' in title_lower


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


def uploads_playlist_id(channel_id):
    if channel_id.startswith("UC"):
        return "UU" + channel_id[2:]
    return None


def fetch_recent_videos_from_channels(tracked):
    print(f"🔍 Checking {len(tracked)} tracked channels...\n")

    video_ids = []
    video_meta = {}

    for channel_id, info in tracked.items():
        playlist_id = uploads_playlist_id(channel_id)
        if not playlist_id:
            continue

        channel_title = info.get("title", channel_id) if isinstance(info, dict) else info

        if is_excluded_channel(channel_title):
            print(f"  🚫 Skipping {channel_title} (excluded keyword)")
            continue

        try:
            resp = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=VIDEOS_PER_CHANNEL
            ).execute()
        except Exception as e:
            print(f"  ❌ Error fetching {channel_title}: {e}")
            continue

        for item in resp.get('items', []):
            vid = item['contentDetails']['videoId']
            if vid in video_meta:
                continue
            snippet = item['snippet']
            video_meta[vid] = {
                'video_id': vid,
                'title': snippet['title'],
                'channel': channel_title,
                'published_at': snippet['publishedAt'],
                'url': f"https://youtu.be/{vid}",
                'thumbnail': snippet.get('thumbnails', {}).get('medium', {}).get('url'),
                'live_broadcast_content': 'none'
            }
            video_ids.append(vid)

    print(f"   Collected {len(video_ids)} video IDs from all channels\n")
    return video_ids, video_meta


def enrich_videos(video_ids, video_meta):
    if not video_ids:
        return []

    stats_dict = {}
    duration_dict = {}

    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        resp = youtube.videos().list(
            part="statistics,contentDetails,snippet",
            id=",".join(chunk)
        ).execute()

        for item in resp.get('items', []):
            vid = item['id']
            stats = item.get('statistics', {})
            stats_dict[vid] = {
                'view_count': int(stats.get('viewCount', 0)),
                'like_count': int(stats.get('likeCount', 0)),
                'comment_count': int(stats.get('commentCount', 0))
            }
            content = item.get('contentDetails', {})
            duration_dict[vid] = parse_duration(content.get('duration', 'PT0S'))

            lbc = item.get('snippet', {}).get('liveBroadcastContent', 'none')
            if vid in video_meta:
                video_meta[vid]['live_broadcast_content'] = lbc

    results = []
    for vid in video_ids:
        meta = video_meta.get(vid)
        if not meta:
            continue

        meta['view_count'] = stats_dict.get(vid, {}).get('view_count', 0)
        meta['like_count'] = stats_dict.get(vid, {}).get('like_count', 0)
        meta['comment_count'] = stats_dict.get(vid, {}).get('comment_count', 0)
        meta['duration_sec'] = duration_dict.get(vid, 0)

        if not is_missioned_souls_video(meta):
            continue

        if is_short_or_too_short(meta):
            continue

        results.append(meta)

    return results


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

        data = {"username": "Missioned Souls Reactions", "embeds": [embed]}

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

    last_published, sent_ids = load_last_run()
    print(f"📅 Last run timestamp: {last_published[:10] if last_published else 'No bookmark — will be set this run'}")

    tracked = load_tracked_channels()
    print(f"📋 Tracking {len(tracked)} channels\n")

    if not tracked:
        print("⚠️ No tracked channels. Run discovery workflow first.")
        raise SystemExit(0)

    video_ids, video_meta = fetch_recent_videos_from_channels(tracked)
    videos = enrich_videos(video_ids, video_meta)

    videos.sort(key=lambda x: x['published_at'], reverse=False)
    print(f"\n✅ {len(videos)} Missioned Souls reactions (after filtering).")

    if not last_published:
        if videos:
            newest = videos[-1]['published_at']
            save_last_run(newest, [v['video_id'] for v in videos[-50:]])
            print(f"📌 Initial bookmark set to: {newest}")
        print("\n🎉 All done!")
        raise SystemExit(0)

    if FORCE_SEND_ALL:
        new_videos = videos
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=SAFETY_WINDOW_DAYS)).isoformat()
        new_videos = [v for v in videos
                      if v['video_id'] not in sent_ids and v['published_at'] > cutoff]

    print(f"🆕 Found {len(new_videos)} new reactions since last run")

    new_videos.sort(key=lambda x: x['published_at'], reverse=False)

    if new_videos:
        top = new_videos[-MAX_TO_SEND:] if len(new_videos) >= MAX_TO_SEND else new_videos
        send_to_discord(top, max_to_send=MAX_TO_SEND)

        newest = new_videos[-1]['published_at']
        sent_ids.extend(v['video_id'] for v in top)
        save_last_run(newest, sent_ids)
        print(f"📌 Bookmark updated to: {newest}")
    else:
        send_to_discord([], max_to_send=MAX_TO_SEND)
        print("ℹ️ No new videos – bookmark unchanged")

    print("\n🎉 All done!")
