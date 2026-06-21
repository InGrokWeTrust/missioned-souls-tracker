import os
import csv
import time
import json
import re
import requests
import logging
from datetime import datetime
from googleapiclient.discovery import build

# ================== CONFIGURATION ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

MAX_TOTAL_RESULTS = int(os.environ.get("MAX_TOTAL_RESULTS", "150"))
MAX_TO_SEND_PER_ARTIST = int(os.environ.get("MAX_TO_SEND", "10"))  # per artist, but we'll send all
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", "last_run.json")
LOG_FILE = "reaction_tracker.log"

# ================== LOGGING SETUP ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===================================================

youtube = build('youtube', 'v3', developerKey=API_KEY)

try:
    from rapidfuzz import fuzz
    FUZZY_AVAILABLE = True
    logger.info("✅ rapidfuzz loaded")
except ImportError:
    FUZZY_AVAILABLE = False
    logger.warning("rapidfuzz not installed – using basic matching")


# ================== LOAD ARTISTS ==================
def load_artists_from_env():
    artists = []
    i = 1
    default_emojis = {
        "Missioned Souls": "🎤",
        "Hagane": "🎸",
        "The Warning": "⚠️"
    }
    while True:
        name = os.environ.get(f"ARTIST_{i}_NAME")
        webhook = os.environ.get(f"ARTIST_{i}_WEBHOOK")
        if not name:
            break
        if not webhook:
            logger.warning(f"⚠️ Artist '{name}' skipped – no webhook")
            i += 1
            continue
        emoji = default_emojis.get(name.strip(), "🎵")
        base_username = os.environ.get(f"ARTIST_{i}_USERNAME", f"{name} Reactions")
        artists.append({
            "name": name.strip(),
            "webhook_url": webhook,
            "username": f"{emoji} {base_username}",
            "color": int(os.environ.get(f"ARTIST_{i}_COLOR", "0x1e88e5"), 16)
        })
        logger.info(f"✅ Loaded artist {i}: {name} {emoji}")
        i += 1
    return artists

ARTISTS = load_artists_from_env()
if not ARTISTS:
    raise ValueError("❌ No artists loaded!")


# ================== HELPERS ==================
def load_last_run():
    try:
        with open(LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('last_published_at')
    except:
        return None

def save_last_run(published_at):
    with open(LAST_RUN_FILE, 'w', encoding='utf-8') as f:
        json.dump({"last_published_at": published_at}, f, indent=2)
    logger.info(f"💾 Saved global timestamp: {published_at}")

def build_search_query(artists):
    artist_queries = [f'"{a["name"]}"' for a in artists]
    return " OR ".join(artist_queries) + ' (reacts OR reaction OR "first time" OR "react to" OR reacting)'

def parse_duration(duration_str):
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str or 'PT0S')
    if not match:
        return 0
    return int(match.group(1) or 0)*3600 + int(match.group(2) or 0)*60 + int(match.group(3) or 0)

def is_short_or_too_short(video):
    title_lower = video.get('title', '').lower()
    if '#shorts' in title_lower:
        return True
    return video.get('duration_sec', 0) < 120

def match_artist(video_title, video_channel, artists):
    if not FUZZY_AVAILABLE:
        combined = (video_title + " " + video_channel).lower()
        for artist in artists:
            if artist["name"].lower() in combined:
                return artist
        return None
    best = None
    best_score = 0
    combined = f"{video_title} {video_channel}"
    for artist in artists:
        s1 = fuzz.partial_ratio(artist["name"], combined)
        s2 = fuzz.token_sort_ratio(artist["name"], combined)
        score = max(s1, s2)
        if score > best_score and score >= 75:
            best_score = score
            best = artist
    return best

def save_to_csv(artist_name, videos):
    if not videos:
        return
    filename = f"{artist_name.lower().replace(' ', '_')}_reactions.csv"
    fieldnames = ['published_at', 'title', 'channel', 'view_count', 
                  'like_count', 'comment_count', 'video_id', 'url']
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for video in videos:
                row = {k: video.get(k) for k in fieldnames}
                writer.writerow(row)
        logger.info(f"💾 Saved {len(videos)} reactions to {filename}")
    except Exception as e:
        logger.error(f"CSV save error: {e}")

def get_all_reactions():
    all_videos = []
    next_page_token = None
    total_fetched = 0
    search_query = build_search_query(ARTISTS)
    logger.info(f"🔍 Searching for {len(ARTISTS)} artists")

    while True:
        try:
            search_request = youtube.search().list(
                part="snippet",
                q=search_query,
                type="video",
                maxResults=50,
                order="date",
                pageToken=next_page_token
            )
            search_response = search_request.execute()
            items = search_response.get('items', [])
            if not items:
                break

            video_ids = [item['id']['videoId'] for item in items]
            temp_videos = []

            for item in items:
                vid = {
                    'title': item['snippet']['title'],
                    'video_id': item['id']['videoId'],
                    'channel': item['snippet']['channelTitle'],
                    'published_at': item['snippet']['publishedAt'],
                    'url': f"https://youtu.be/{item['id']['videoId']}",
                    'thumbnail': item['snippet'].get('thumbnails', {}).get('medium', {}).get('url')
                }
                temp_videos.append(vid)
                total_fetched += 1

            if video_ids:
                stats_response = youtube.videos().list(
                    part="statistics,contentDetails",
                    id=",".join(video_ids)
                ).execute()

                for item in stats_response.get('items', []):
                    vid_id = item['id']
                    for video in temp_videos:
                        if video['video_id'] == vid_id:
                            stats = item.get('statistics', {})
                            video['view_count'] = int(stats.get('viewCount', 0))
                            video['like_count'] = int(stats.get('likeCount', 0))
                            video['comment_count'] = int(stats.get('commentCount', 0))
                            video['duration_sec'] = parse_duration(item['contentDetails'].get('duration'))
                            break

            valid_videos = [v for v in temp_videos if not is_short_or_too_short(v)]
            all_videos.extend(valid_videos)
            logger.info(f"Fetched {len(temp_videos)} | Kept {len(valid_videos)} | Total: {len(all_videos)}")

            next_page_token = search_response.get('nextPageToken')
            if not next_page_token or total_fetched >= MAX_TOTAL_RESULTS:
                break
            time.sleep(0.8)
        except Exception as e:
            logger.error(f"YouTube API error: {e}")
            break

    all_videos.sort(key=lambda x: x['published_at'], reverse=True)
    logger.info(f"✅ Total valid reactions: {len(all_videos)}")
    return all_videos

def send_to_discord(videos, artist):
    if not videos or not artist.get("webhook_url"):
        return
    logger.info(f"📨 Sending {len(videos)} reactions for {artist['name']}")
    for video in videos:
        embed = {
            "title": video['title'],
            "url": video['url'],
            "color": artist['color'],
            "image": {"url": video.get('thumbnail')} if video.get('thumbnail') else None,
            "fields": [
                {"name": "Reactor", "value": video['channel'], "inline": True},
                {"name": "Views", "value": f"{video.get('view_count', 0):,}", "inline": True},
                {"name": "Likes", "value": f"{video.get('like_count', 0):,}", "inline": True},
            ],
            "timestamp": video['published_at']
        }
        data = {"username": artist['username'], "embeds": [embed]}
        try:
            response = requests.post(artist['webhook_url'], json=data, timeout=10)
            if response.status_code == 204:
                logger.info(f"✅ Sent: {video['title'][:65]}...")
            else:
                logger.error(f"Discord error {response.status_code}")
        except Exception as e:
            logger.error(f"Send failed: {e}")
        time.sleep(1.3)


# ===================== MAIN =====================
if __name__ == "__main__":
    logger.info("🚀 Multi-Artist Tracker Started")
    logger.info(f"Loaded {len(ARTISTS)} artists")

    last_published = load_last_run()
    logger.info(f"📅 Last run timestamp: {last_published if last_published else 'None (first run)'}")

    videos = get_all_reactions()

    # Determine new videos using global timestamp
    if FORCE_SEND_ALL or not last_published:
        new_videos = videos
        logger.info(f"🔄 Force mode: treating all {len(new_videos)} as new")
    else:
        new_videos = [v for v in videos if v['published_at'] > last_published]
        logger.info(f"🆕 Found {len(new_videos)} new videos since {last_published}")

    # ✅ SAVE BOOKMARK IMMEDIATELY – even before sending
    # Use the newest video's timestamp (first in sorted list)
    if new_videos:
        new_timestamp = new_videos[0]['published_at']
        save_last_run(new_timestamp)
        logger.info(f"📌 Bookmark updated to {new_timestamp}")
    else:
        logger.info("ℹ️ No new videos – bookmark unchanged")

    # Group new videos by artist
    grouped = {a["name"]: [] for a in ARTISTS}
    for video in new_videos:
        matched = match_artist(video['title'], video['channel'], ARTISTS)
        if matched:
            grouped[matched["name"]].append(video)
        else:
            logger.info(f"ℹ️ No artist match: {video['title'][:50]}...")

    # Send each artist's new videos (no limit per artist – send all)
    for artist in ARTISTS:
        artist_name = artist["name"]
        artist_vids = grouped.get(artist_name, [])
        if artist_vids:
            send_to_discord(artist_vids, artist)
            # Save CSV for this artist (all videos of this artist)
            full_artist_vids = [v for v in videos if match_artist(v['title'], v['channel'], ARTISTS) and match_artist(v['title'], v['channel'], ARTISTS)["name"] == artist_name]
            save_to_csv(artist_name, full_artist_vids)

    # Save combined CSV and HTML (optional)
    with open("reactions_all.csv", 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['published_at', 'title', 'channel', 'view_count', 
                      'like_count', 'comment_count', 'video_id', 'url']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for video in videos:
            row = {k: video.get(k) for k in fieldnames}
            writer.writerow(row)
    logger.info("💾 Saved reactions_all.csv")

    html_content = """<!DOCTYPE html>
<html>
<head><title>Reaction Videos</title>
<style>
body { font-family: Arial; margin:20px; background:#f4f4f4; }
h1 { color:#1e3a8a; text-align:center; }
table { width:100%; border-collapse:collapse; background:white; }
th, td { padding:12px; border:1px solid #ddd; }
th { background:#1e3a8a; color:white; }
tr:hover { background:#e0f2fe; }
.stats { text-align:center; font-size:18px; margin:20px; }
</style>
</head>
<body>
<h1>🎥 Reaction Videos</h1>
<p class="stats">Total: """ + str(len(videos)) + """ | New: """ + str(len(new_videos)) + """</p>
<table><thead><tr><th>Date</th><th>Channel</th><th>Title</th><th>Views</th><th>Likes</th><th>Link</th></tr></thead><tbody>"""
    for v in videos:
        date = v['published_at'][:10] if v['published_at'] else ""
        html_content += f"<tr><td>{date}</td><td>{v['channel']}</td><td>{v['title']}</td><td>{v.get('view_count',0):,}</td><td>{v.get('like_count',0):,}</td><td><a href='{v['url']}' target='_blank'>Watch</a></td></tr>"
    html_content += "</tbody></table></body></html>"
    with open("reactions_all.html", "w", encoding='utf-8') as f:
        f.write(html_content)
    logger.info("🌐 reactions_all.html created")

    logger.info("🎉 All done!")
