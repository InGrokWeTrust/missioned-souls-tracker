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
MAX_TO_SEND_PER_ARTIST = int(os.environ.get("MAX_TO_SEND", "6"))
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
    logger.info("✅ rapidfuzz loaded successfully")
except ImportError:
    FUZZY_AVAILABLE = False
    logger.warning("rapidfuzz not installed. Falling back to basic matching.")


# ================== LOAD ARTISTS (Variables + Secrets) ==================
def load_artists_from_env():
    artists = []
    i = 1
    
    # Default emojis for known artists
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
            logger.warning(f"⚠️ Artist '{name}' skipped - No webhook found in Secrets")
            i += 1
            continue
        
        emoji = default_emojis.get(name.strip(), "🎵")
        base_username = os.environ.get(f"ARTIST_{i}_USERNAME", f"{name} Reactions")
        
        artist = {
            "name": name.strip(),
            "webhook_url": webhook,
            "username": f"{emoji} {base_username}",
            "color": int(os.environ.get(f"ARTIST_{i}_COLOR", "0x1e88e5"), 16)
        }
        
        artists.append(artist)
        logger.info(f"✅ Loaded artist {i}: {name} {emoji}")
        i += 1
    
    return artists


ARTISTS = load_artists_from_env()

if not ARTISTS:
    raise ValueError("❌ No artists loaded! Please check GitHub Variables and Secrets.")


# ================== HELPER FUNCTIONS ==================
def load_last_run():
    try:
        with open(LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}


def save_last_run(last_run_dict):
    with open(LAST_RUN_FILE, 'w', encoding='utf-8') as f:
        json.dump(last_run_dict, f, indent=2)
    logger.info(f"💾 Saved last run timestamps")


def build_search_query(artists):
    artist_queries = [f'"{artist["name"]}"' for artist in artists]
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


def match_artist(video_title: str, video_channel: str, artists):
    if not FUZZY_AVAILABLE:
        title_lower = video_title.lower()
        channel_lower = video_channel.lower()
        for artist in artists:
            if artist["name"].lower() in title_lower or artist["name"].lower() in channel_lower:
                return artist
        return None

    best_match = None
    best_score = 0
    combined = f"{video_title} {video_channel}"

    for artist in artists:
        score1 = fuzz.partial_ratio(artist["name"], combined)
        score2 = fuzz.token_sort_ratio(artist["name"], combined)
        score = max(score1, score2)

        if score > best_score and score >= 75:
            best_score = score
            best_match = artist

    return best_match


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
        logger.error(f"Failed to save CSV for {artist_name}: {e}")


def get_all_reactions():
    all_videos = []
    next_page_token = None
    total_fetched = 0

    search_query = build_search_query(ARTISTS)
    logger.info(f"🔍 Starting unified search for {len(ARTISTS)} artists")

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
    logger.info(f"✅ Total valid reactions found: {len(all_videos)}")
    return all_videos


def send_to_discord(videos, artist, max_to_send=6):
    if not videos or not artist.get("webhook_url"):
        return

    logger.info(f"📨 Sending up to {max_to_send} reactions for {artist['name']}")

    for video in videos[:max_to_send]:
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
            logger.error(f"Failed to send: {e}")

        time.sleep(1.3)


# ===================== MAIN =====================
if __name__ == "__main__":
    logger.info("🚀 Multi-Artist Reaction Tracker Started")
    logger.info(f"Loaded {len(ARTISTS)} artists")

    last_run = load_last_run()
    videos = get_all_reactions()

    artist_videos = {artist["name"]: [] for artist in ARTISTS}

    for video in videos:
        matched = match_artist(video['title'], video['channel'], ARTISTS)
        if matched:
            artist_videos[matched["name"]].append(video)

    new_last_run = last_run.copy()
    any_new = False

    for artist in ARTISTS:
        artist_name = artist["name"]
        artist_vids = artist_videos.get(artist_name, [])

        logger.info(f"\n{'═' * 70}")
        logger.info(f"🎤 Processing: {artist_name} | Total found: {len(artist_vids)}")

        if FORCE_SEND_ALL or artist_name not in last_run:
            # Force mode: send first 30 (or all if you prefer) and update last_run to the most recent
            # To avoid missing videos, we send all (or cap at a high number) and update last_run to the newest.
            # For safety, we send up to 30 but save last_run as the newest.
            new_videos = artist_vids[:30] if len(artist_vids) > 30 else artist_vids
            logger.info(f"🔄 Force mode - Sending {len(new_videos)} videos")
            if new_videos:
                send_to_discord(new_videos, artist, MAX_TO_SEND_PER_ARTIST)
                # Save last_run as the most recent video's timestamp (newest)
                new_last_run[artist_name] = new_videos[0]['published_at']
                any_new = True
                save_to_csv(artist_name, artist_vids)
        else:
            last_time = last_run[artist_name]
            # Get all videos newer than last_time
            all_new = [v for v in artist_vids if v['published_at'] > last_time]
            logger.info(f"🆕 Found {len(all_new)} new reactions")

            if all_new:
                any_new = True
                # Send only the most recent MAX_TO_SEND_PER_ARTIST videos
                to_send = all_new[:MAX_TO_SEND_PER_ARTIST]
                send_to_discord(to_send, artist, MAX_TO_SEND_PER_ARTIST)
                # Update last_run to the OLDEST video we actually sent (so older unsent will be picked next time)
                # If we sent fewer than all_new, we want to catch the remaining ones next run.
                # Set last_run to the timestamp of the last video we sent (the oldest among sent)
                new_last_run[artist_name] = to_send[-1]['published_at']
                save_to_csv(artist_name, artist_vids)

    if any_new:
        save_last_run(new_last_run)

    logger.info("🎉 All artists processed successfully!")
