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
MAX_TO_SEND_PER_ARTIST = int(os.environ.get("MAX_TO_SEND", "6"))
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", "last_run.json")

# ================== LOAD ARTISTS FROM ENV ==================
def load_artists():
    artists = []
    i = 1
    while True:
        name = os.environ.get(f"ARTIST_{i}_NAME")
        webhook = os.environ.get(f"ARTIST_{i}_WEBHOOK")
        if not name:
            break
        if not webhook:
            print(f"⚠️ Artist '{name}' skipped – no webhook found")
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

ARTISTS = load_artists()
if not ARTISTS:
    raise ValueError("No artists loaded! Set ARTIST_1_NAME and ARTIST_1_WEBHOOK.")

# ===========================================

youtube = build('youtube', 'v3', developerKey=API_KEY)

# Try to import rapidfuzz for better matching
try:
    from rapidfuzz import fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    print("⚠️ rapidfuzz not installed – using basic matching")

# ================== HELPER FUNCTIONS ==================
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

def match_artist(video_title, video_channel):
    """Return the artist dict that best matches the video, or None."""
    if not FUZZY_AVAILABLE:
        combined_lower = (video_title + " " + video_channel).lower()
        for artist in ARTISTS:
            if artist["name"].lower() in combined_lower:
                return artist
        return None

    best = None
    best_score = 0
    combined = f"{video_title} {video_channel}"
    for artist in ARTISTS:
        score1 = fuzz.partial_ratio(artist["name"], combined)
        score2 = fuzz.token_sort_ratio(artist["name"], combined)
        score = max(score1, score2)
        if score > best_score and score >= 75:
            best_score = score
            best = artist
    return best

# ================== FETCH REACTIONS ==================
def get_reactions_with_stats():
    all_videos = []
    next_page_token = None
    total_fetched = 0

    # Build combined search query
    artist_queries = [f'"{a["name"]}"' for a in ARTISTS]
    search_query = " OR ".join(artist_queries) + ' (reacts OR reaction OR "first time" OR "react to" OR reacting)'
    print(f"🔍 Searching for: {', '.join([a['name'] for a in ARTISTS])}\n")

    while True:
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
    print(f"\n✅ Found {len(all_videos)} total reactions (after filtering).")
    return all_videos

# ================== SEND TO DISCORD ==================
def send_to_discord_for_artist(videos, artist, max_to_send=5):
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
                print(f"✅ Sent to {artist['name']}: {video['title'][:60]}...")
            else:
                print(f"❌ Discord error {response.status_code} for {artist['name']}")
        except Exception as e:
            print(f"❌ Failed to send to {artist['name']}: {e}")
        time.sleep(1.3)

# ===================== MAIN =====================
if __name__ == "__main__":
    print("🚀 Multi-Artist Reaction Tracker Started\n")

    last_published = load_last_run()
    print(f"📅 Last run timestamp: {last_published[:10] if last_published else 'First run'}")

    videos = get_reactions_with_stats()

    # Determine new videos using global timestamp
    if FORCE_SEND_ALL or not last_published:
        new_videos = videos
        print(f"🔄 Force mode: Sending latest {len(new_videos)} videos")
    else:
        new_videos = [v for v in videos if v['published_at'] > last_published]
        print(f"🆕 Found {len(new_videos)} new reactions since last run")

    # Group new videos by artist
    grouped = {a["name"]: [] for a in ARTISTS}
    for video in new_videos:
        artist = match_artist(video['title'], video['channel'])
        if artist:
            grouped[artist["name"]].append(video)
        else:
            # Optional: send to a fallback channel (not implemented)
            print(f"⚠️ No artist match for: {video['title'][:50]}...")

    # Send to each artist's Discord
    for artist in ARTISTS:
        artist_name = artist["name"]
        artist_vids = grouped.get(artist_name, [])
        if artist_vids:
            send_to_discord_for_artist(artist_vids, artist, MAX_TO_SEND_PER_ARTIST)

    # Save CSV (all videos, not just new)
    with open("reactions_all.csv", 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['title', 'channel', 'published_at', 'view_count',
                     'like_count', 'comment_count', 'video_id', 'url']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for video in videos:
            row = {k: video.get(k) for k in fieldnames}
            writer.writerow(row)
    print("💾 Saved all reactions to reactions_all.csv")

    # Generate HTML (same as before, but using all videos)
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reaction Videos</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #f4f4f4; }
    h1 { color: #1e3a8a; text-align: center; }
    table { width: 100%; border-collapse: collapse; background: white; }
    th, td { padding: 12px; border: 1px solid #ddd; text-align: left; }
    th { background: #1e3a8a; color: white; }
    tr:hover { background: #e0f2fe; }
    .stats { text-align: center; font-size: 18px; margin: 20px; font-weight: bold; }
  </style>
</head>
<body>
  <h1>🎥 Reaction Videos</h1>
  <p class="stats">Total Reactions: """ + str(len(videos)) + """ | New: """ + str(len(new_videos)) + """</p>
  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Channel</th>
        <th>Title</th>
        <th>Views</th>
        <th>Likes</th>
        <th>Link</th>
      </tr>
    </thead>
    <tbody>"""
    for v in videos:
        date = v['published_at'][:10] if v['published_at'] else ""
        html_content += f"""
      <tr>
        <td>{date}</td>
        <td>{v['channel']}</td>
        <td>{v['title']}</td>
        <td>{v.get('view_count', 0):,}</td>
        <td>{v.get('like_count', 0):,}</td>
        <td><a href="{v['url']}" target="_blank">Watch →</a></td>
      </tr>"""
    html_content += """
    </tbody>
  </table>
</body>
</html>"""

    with open("reactions_all.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("🌐 Static website updated (reactions_all.html)")

    # Update bookmark only if we sent something
    if new_videos:
        # Use the latest video's timestamp (newest of all new)
        save_last_run(new_videos[0]['published_at'])

    print("\n🎉 All done!")"
