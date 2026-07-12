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

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
MAX_TOTAL_RESULTS = int(os.environ.get("MAX_TOTAL_RESULTS", "80"))
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND", "6"))
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", "last_run.json")
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


def parse_duration(duration_str):
    """Convert ISO 8601 duration string (e.g., PT2M30S) to total seconds."""
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
    """Return True if video is a Short or duration < 120 seconds."""
    title_lower = video.get('title', '').lower()
    if '#shorts' in title_lower:
        return True
    duration_sec = video.get('duration_sec', 0)
    if duration_sec < 120:
        return True
    return False


def get_reactions_with_stats():
    all_videos = []
    next_page_token = None
    total_fetched = 0

    print(f"🔍 Searching latest reactions for {CHANNEL_NAME}...\n")

    while True:
        search_request = youtube.search().list(
            part="snippet",
            q=f'"{CHANNEL_NAME}" (reacts OR reaction OR "first time" OR "react to" OR reacting)',
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
                content_details = item.get('contentDetails', {})
                duration_str = content_details.get('duration', 'PT0S')
                duration_dict[vid_id] = parse_duration(duration_str)
            
            for video in temp_videos:
                vid_id = video['video_id']
                vid_stats = stats_dict.get(vid_id, {})
                video['view_count'] = vid_stats.get('view_count', 0)
                video['like_count'] = vid_stats.get('like_count', 0)
                video['comment_count'] = vid_stats.get('comment_count', 0)
                video['duration_sec'] = duration_dict.get(vid_id, 0)

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


def send_to_discord(videos, max_to_send=5):
    if not videos:
        print("⚠️ No new videos to send.")
        return

    print(f"\n📨 Sending {min(max_to_send, len(videos))} new reactions to Discord...\n")

    for video in videos[:max_to_send]:
        embed = {
            "title": video['title'],
            "url": video['url'],
            "color": 0x1e88e5,
            "image": {"url": video.get('thumbnail')} if video.get('thumbnail') else None,
            "fields": [
                {"name": "Channel", "value": video['channel'], "inline": True},
                {"name": "Views", "value": f"{video.get('view_count', 0):,}", "inline": True},
                {"name": "Likes", "value": f"{video.get('like_count', 0):,}", "inline": True},
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
                print(f"✅ Sent: {video['title'][:60]}...")
            else:
                print(f"❌ Discord error {response.status_code}")
        except Exception as e:
            print(f"❌ Failed to send: {e}")
        
        time.sleep(1.3)


# ===================== MAIN =====================
if __name__ == "__main__":
    print("🚀 Missioned Souls Reaction Tracker Started\n")
    
    last_published = load_last_run()
    print(f"📅 Last run timestamp: {last_published[:10] if last_published else 'First run'}")

    videos = get_reactions_with_stats()

    if FORCE_SEND_ALL or not last_published:
        new_videos = videos
        print(f"🔄 Force mode: Sending latest {len(new_videos)} videos")
    else:
        new_videos = [v for v in videos if v['published_at'] > last_published]
        print(f"🆕 Found {len(new_videos)} new reactions since last run")

    # Save CSV (duration_sec not saved, only original fields)
    with open("missioned_souls_reactions.csv", 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['title', 'channel', 'published_at', 'view_count', 
                     'like_count', 'comment_count', 'video_id', 'url']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for video in videos:
            video_copy = {k: v for k, v in video.items() if k in fieldnames}
            writer.writerow(video_copy)

    print(f"💾 Saved to missioned_souls_reactions.csv")

    # Generate HTML
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Missioned Souls Reactions</title>
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
  <h1>🎥 Missioned Souls - Reaction Videos</h1>
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

    with open("missioned_souls_reactions.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print("🌐 Static website updated")

    # --- Send to Discord: oldest first, newest last ---
    if new_videos:
        to_send = new_videos[::-1]   # reverse to oldest first
        send_to_discord(to_send, max_to_send=MAX_TO_SEND)
        # Update bookmark to the newest video (the one at the top of the original list)
        save_last_run(new_videos[0]['published_at'])
    else:
        send_to_discord([], max_to_send=MAX_TO_SEND)  # will print "No new videos"

    print("\n🎉 All done!")