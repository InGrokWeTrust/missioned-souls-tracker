import os
import time
import json
import requests
from datetime import datetime, timezone
from googleapiclient.discovery import build

# ================== READ CONFIG FROM ENVIRONMENT ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND", "6"))
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LIVE_LAST_RUN_FILE = os.environ.get("LIVE_LAST_RUN_FILE", "live_last_run.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

# Cap on stored IDs to prevent unbounded growth
MAX_STORED_IDS = 200

# ===========================================

youtube = build('youtube', 'v3', developerKey=API_KEY)


def load_state():
    """Load the sent video IDs and last check time."""
    try:
        with open(LIVE_LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            sent_ids = data.get('sent_video_ids', [])
            last_check = data.get('last_check')
            # Backward compatibility: if old format, return empty list
            if not isinstance(sent_ids, list):
                sent_ids = []
            return sent_ids, last_check
    except (FileNotFoundError, json.JSONDecodeError):
        return [], None


def save_state(sent_ids):
    """Save the sent video IDs (capped) and current time."""
    # Cap at MAX_STORED_IDS, keeping the most recent
    sent_ids = sent_ids[-MAX_STORED_IDS:]
    data = {
        "sent_video_ids": sent_ids,
        "last_check": datetime.now(timezone.utc).isoformat()
    }
    with open(LIVE_LAST_RUN_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    print(f"💾 Updated {LIVE_LAST_RUN_FILE} → {len(sent_ids)} IDs tracked")


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
    try:
        ts = published_at_iso.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts)
        return dt.strftime('%I:%M %p').lstrip('0')
    except Exception:
        return ""


def find_active_live_streams():
    """Search YouTube for active live streams mentioning Missioned Souls."""
    print(f"🔍 Searching for ACTIVE live streams about {CHANNEL_NAME}...\n")

    response = youtube.search().list(
        part="snippet",
        q=f'"{CHANNEL_NAME}"',
        type="video",
        eventType="live",
        maxResults=50,
        order="date"
    ).execute()

    items = response.get('items', [])
    print(f"📊 YouTube returned {len(items)} live streams\n")

    streams = []
    for item in items:
        snippet = item['snippet']
        video_id = item['id'].get('videoId')
        if not video_id:
            continue

        title = snippet['title']
        # Double-check title actually mentions Missioned Souls
        if CHANNEL_NAME.lower() not in title.lower():
            print(f"  ⏭️  [title mismatch] {snippet['channelTitle'][:24]:24} | {title[:55]}")
            continue

        streams.append({
            'title': title,
            'video_id': video_id,
            'channel': snippet['channelTitle'],
            'published_at': snippet['publishedAt'],
            'url': f"https://youtu.be/{video_id}",
            'thumbnail': snippet.get('thumbnails', {}).get('medium', {}).get('url'),
            'live_broadcast_content': snippet.get('liveBroadcastContent', 'live')
        })

    print(f"\n✅ {len(streams)} live streams match (title contains '{CHANNEL_NAME}')")
    return streams


def send_to_discord(streams, run_start=None):
    if not streams:
        print("⚠️ No live streams to send.")
        return

    print(f"\n📨 Sending {len(streams)} live stream(s) to Discord...\n")

    for stream in streams:
        age = humanize_ago(stream['published_at'], run_start)
        published_str = format_published_time(stream['published_at'])
        posted_value = f"{published_str} ({age})" if published_str else age

        title = f"🔴 LIVE: {stream['title']}"

        embed = {
            "title": title,
            "url": stream['url'],
            "color": 0xe53935,  # red for live
            "image": {"url": stream.get('thumbnail')} if stream.get('thumbnail') else None,
            "fields": [
                {"name": "Channel", "value": stream['channel'], "inline": True},
                {"name": "Started", "value": posted_value, "inline": True},
            ],
            "timestamp": stream['published_at']
        }

        data = {
            "username": "Missioned Souls Reactions",
            "embeds": [embed]
        }

        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
            if response.status_code == 204:
                print(f"✅ Sent: [{posted_value}] {stream['title'][:60]}...")
            else:
                print(f"❌ Discord error {response.status_code}")
        except Exception as e:
            print(f"❌ Failed to send: {e}")
        time.sleep(1.3)


# ===================== MAIN =====================
if __name__ == "__main__":
    print("🚀 Missioned Souls Live Stream Checker Started\n")

    run_start = datetime.now(timezone.utc)
    print(f"⏰ Run started at: {run_start.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    sent_ids, last_check = load_state()
    print(f"📅 Last check: {last_check[:19] if last_check else 'None (first run)'}")
    print(f"📋 Previously sent IDs: {len(sent_ids)}\n")

    streams = find_active_live_streams()

    if not streams:
        print("ℹ️ No active Missioned Souls live streams found.")
        # Still save state to update last_check
        save_state(sent_ids)
        print("\n🎉 All done!")
        raise SystemExit(0)

    if FORCE_SEND_ALL:
        new_streams = streams
        print(f"🔄 Force mode: Sending all {len(new_streams)} streams")
    else:
        # Filter by video ID, not timestamp
        new_streams = [s for s in streams if s['video_id'] not in sent_ids]
        print(f"🆕 {len(new_streams)} new live streams (not previously sent)")

    if new_streams:
        new_streams.sort(key=lambda x: x['published_at'], reverse=False)
        top_streams = new_streams[-MAX_TO_SEND:] if len(new_streams) >= MAX_TO_SEND else new_streams
        send_to_discord(top_streams, run_start=run_start)

        # Record the IDs we just sent
        for s in top_streams:
            if s['video_id'] not in sent_ids:
                sent_ids.append(s['video_id'])

        save_state(sent_ids)
        print(f"📌 {len(sent_ids)} total IDs tracked")
    else:
        print("ℹ️ No new live streams – state unchanged")
        # Still update last_check
        save_state(sent_ids)

    print("\n🎉 All done!")
