import os
import csv
import time
import json
import re
import requests
from datetime import datetime
from googleapiclient.discovery import build

# ================== READ CONFIG FROM ENVIRONMENT ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

MAX_TOTAL_RESULTS = int(os.environ.get("MAX_TOTAL_RESULTS", "80"))
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND", "6"))
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", "last_run.json")

# ================== LOAD ARTISTS ==================
def load_artists():
    artists = []
    i = 1
    while True:
        name = os.environ.get(f"ARTIST_{i}_NAME")
        webhook = os.environ.get(f"ARTIST_{i}_WEBHOOK")
        if not name:
            break
        if not webhook:
            print(f"⚠️ Artist '{name}' skipped – no webhook")
            i += 1
            continue
        artists.append({
            "name": name.strip(),
            "webhook_url": webhook,
            "username": os.environ.get(f"ARTIST_{i}_USERNAME", f"{name} Reactions"),
            "color": int(os.environ.get(f"ARTIST_{i}_COLOR", "0x1e88e5"), 16)
        })
        i += 1
    return artists

artists = load_artists()

# Fallback to single‑artist mode (exactly like your original script)
if not artists:
    CHANNEL_NAME = os.environ.get("CHANNEL_NAME")
    DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
    if CHANNEL_NAME and DISCORD_WEBHOOK_URL:
        artists = [{
            "name": CHANNEL_NAME,
            "webhook_url": DISCORD_WEBHOOK_URL,
            "username": f"{CHANNEL_NAME} Reactions",
            "color": 0x1e88e5
        }]
        print(f"✅ Using single‑artist mode for '{CHANNEL_NAME}'")
    else:
        raise ValueError("No artists defined! Set ARTIST_1_NAME+WEBHOOK or CHANNEL_NAME+DISCORD_WEBHOOK_URL.")

# ===================================================

youtube = build('youtube', 'v3', developerKey=API_KEY)

# ---------- last_run helpers (per‑artist) ----------
def load_last_run():
    """Load the entire last_run.json dict."""
    try:
        with open(LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_last_run(data):
    with open(LAST_RUN_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    print(f"💾 Updated {LAST_RUN_FILE}")

# ---------- Duration parsing (unchanged) ----------
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

def is_short_or_too_short(video):
    title_lower = video.get('title', '').lower()
    if '#shorts' in title_lower:
        return True
    return video.get('duration_sec', 0) < 120

# ---------- Search per artist (EXACT same query as single‑artist) ----------
def get_reactions_for_artist(artist_name):
    """Fetch reaction videos for a single artist using the proven query."""
    all_videos = []
    next_page_token = None
    total_fetched = 0

    print(f"\n🔍 Searching latest reactions for {artist_name}...")

    while True:
        search_request = youtube.search().list(
            part="snippet",
            q=f'"{artist_name}" (reacts OR reaction OR "first time" OR "react to" OR reacting)',
            type="video",
            maxResults=50,
            order="date",
            pageToken=next_page_token
        )
        search_response = search_request.execute()
        items = search_response.get('items', [])
        if not items:
            break

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
                'thumbnail': item['snippet'].get('thumbnails', {}).get('medium', {}).get('url')
            }
            temp_videos.append(video)
            video_ids.append(video_id)
            total_fetched += 1

        if video_ids:
            stats_request = youtube.videos().list(
                part="statistics,contentDetails",
                id=",".join(video_ids)
            )
            stats_response = stats_request.execute()

            stats_dict = {}
            duration_dict = {}
            for item in stats_response.get('items', []):
                vid_id = item['id']
                stats = item.get('statistics', {})
                stats_dict[vid_id] = {
                    'view_count': int(stats.get('viewCount', 0)),
                    'like_count': int(stats.get('likeCount', 0)),
                    'comment_count': int(stats.get('commentCount', 0))
                }
                duration_dict[vid_id] = parse_duration(item.get('contentDetails', {}).get('duration', 'PT0S'))

            for video in temp_videos:
                vid_id = video['video_id']
                vid_stats = stats_dict.get(vid_id, {})
                video['view_count'] = vid_stats.get('view_count', 0)
                video['like_count'] = vid_stats.get('like_count', 0)
                video['comment_count'] = vid_stats.get('comment_count', 0)
                video['duration_sec'] = duration_dict.get(vid_id, 0)

        # Filter out Shorts and videos under 2 minutes
        filtered_batch = [v for v in temp_videos if not is_short_or_too_short(v)]
        all_videos.extend(filtered_batch)

        for video in temp_videos:
            views = video.get('view_count', 0)
            status = "⏭️ (filtered)" if is_short_or_too_short(video) else "✅"
            print(f"{views:8,} views | {video['title'][:70]} {status}")

        next_page_token = search_response.get('nextPageToken')
        if not next_page_token or total_fetched >= MAX_TOTAL_RESULTS:
            break
        time.sleep(0.7)

    all_videos.sort(key=lambda x: x['published_at'], reverse=True)
    print(f"✅ Found {len(all_videos)} valid reactions for {artist_name} (after filtering).")
    return all_videos

# ---------- Send to Discord (per artist) ----------
def send_to_discord(videos, artist, max_to_send=5):
    if not videos:
        return
    print(f"\n📨 Sending {min(max_to_send, len(videos))} reactions for {artist['name']}...")
    for video in videos[:max_to_send]:
        embed = {
            "title": video['title'],
            "url": video['url'],
            "color": artist['color'],
            "image": {"url": video.get('thumbnail')} if video.get('thumbnail') else None,
            "fields": [
                {"name": "Channel", "value": video['channel'], "inline": True},
                {"name": "Views", "value": f"{video.get('view_count', 0):,}", "inline": True},
                {"name": "Likes", "value": f"{video.get('like_count', 0):,}", "inline": True},
            ],
            "timestamp": video['published_at']
        }
        data = {"username": artist['username'], "embeds": [embed]}
        try:
            response = requests.post(artist['webhook_url'], json=data, timeout=10)
            if response.status_code == 204:
                print(f"✅ Sent to {artist['name']}: {video['title'][:60]}...")
            else:
                print(f"❌ Discord error {response.status_code} for {artist['name']}")
        except Exception as e:
            print(f"❌ Failed to send to {artist['name']}: {e}")
        time.sleep(1.3)

# ---------- Save per‑artist CSV (optional) ----------
def save_artist_csv(artist_name, videos):
    if not videos:
        return
    filename = f"{artist_name.lower().replace(' ', '_')}_reactions.csv"
    fieldnames = ['title', 'channel', 'published_at', 'view_count',
                  'like_count', 'comment_count', 'video_id', 'url']
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for video in videos:
                row = {k: video.get(k) for k in fieldnames}
                writer.writerow(row)
        print(f"💾 Saved {len(videos)} reactions to {filename}")
    except Exception as e:
        print(f"❌ CSV save failed for {artist_name}: {e}")

# ===================== MAIN =====================
if __name__ == "__main__":
    print("🚀 Multi-Artist Reaction Tracker Started\n")
    last_run_data = load_last_run()
    print(f"📅 Last run timestamps: {last_run_data}")

    any_new = False
    new_last_run = last_run_data.copy()

    for artist in artists:
        artist_name = artist["name"]
        last_time = last_run_data.get(artist_name)  # None if first run

        # Fetch videos using the proven search
        all_videos = get_reactions_for_artist(artist_name)

        # Determine new videos
        if FORCE_SEND_ALL or not last_time:
            new_videos = all_videos
            print(f"🔄 Force mode for {artist_name}: Sending {len(new_videos)} videos")
        else:
            new_videos = [v for v in all_videos if v['published_at'] > last_time]
            print(f"🆕 {artist_name}: Found {len(new_videos)} new videos since {last_time}")

        if new_videos:
            # Send to Discord
            send_to_discord(new_videos, artist, MAX_TO_SEND)
            # Save CSV for this artist (all videos, not just new)
            save_artist_csv(artist_name, all_videos)

            # Update bookmark to the newest video's timestamp
            new_last_run[artist_name] = new_videos[0]['published_at']
            any_new = True

        # If no new videos, keep the existing timestamp
        else:
            # If artist didn't exist in last_run_data, set a default old timestamp
            if artist_name not in new_last_run:
                new_last_run[artist_name] = "1970-01-01T00:00:00Z"

    # Save all bookmarks if anything changed
    if any_new:
        save_last_run(new_last_run)
        print("💾 Updated last_run.json with new timestamps")

    # Also generate a combined CSV and HTML (optional – same as single‑artist)
    # We can collect all videos from all artists by merging, but that's extra.
    # For simplicity, we skip combined HTML to avoid confusion.
    print("\n🎉 All artists processed successfully!") 