import os
import time
import json
import requests
from datetime import datetime, timezone
from googleapiclient.discovery import build
from notification_history import read_ids, save_ids

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


def send_to_discord(streams, run_start=None, seen_ids=None):
    """Send live alerts once and durably record each confirmed webhook."""
    if not streams:
        print("⚠️ No live streams to send.")
        return []
    if seen_ids is None:
        seen_ids = set()
    delivered = []
    print(f"\n📨 Sending {len(streams)} live stream(s) to Discord...\n")

    for stream in streams:
        vid = stream["video_id"]
        if vid in seen_ids:
            print(f"⏭️ Already delivered: {vid}")
            continue
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
        except Exception as e:
            raise RuntimeError(f"Discord live delivery failed for {vid}") from e
        if response.status_code != 204:
            raise RuntimeError(f"Discord live HTTP {response.status_code} for {vid}")
        save_ids("live", [vid])
        seen_ids.add(vid)
        delivered.append(stream)
        print(f"✅ Sent: [{posted_value}] {stream['title'][:60]}...")
        time.sleep(1.3)
    return delivered


# ===================== MAIN =====================
def run():
    print("🚀 Missioned Souls Live Stream Checker Started\n")
    # Both ledgers matter: a published VOD must not be announced again as live,
    # and an already announced live must not later post as a reaction duplicate.
    reaction_ids = read_ids("reactions")
    live_ids = read_ids("live")
    already_delivered = reaction_ids | live_ids

    run_start = datetime.now(timezone.utc)
    print(f"⏰ Run started at: {run_start.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    sent_ids, last_check = load_state()
    already_delivered.update(sent_ids)
    print(f"📅 Last check: {last_check[:19] if last_check else 'None (first run)'}")
    print(f"📋 Previously sent IDs: {len(already_delivered)}\n")

    streams = find_active_live_streams()
    if not streams:
        print("ℹ️ No active Missioned Souls live streams found.")
        save_state(sent_ids)
        print("\n🎉 All done!")
        return []

    seen_in_scan = set()
    new_streams = []
    for stream in streams:
        vid = stream['video_id']
        if vid not in already_delivered and vid not in seen_in_scan:
            new_streams.append(stream)
            seen_in_scan.add(vid)
    new_streams.sort(key=lambda v: (v['published_at'], v['video_id']))
    print(f"🆕 {len(new_streams)} new live streams (durable IDs checked; "
          f"FORCE_SEND_ALL never bypasses confirmed delivery history)")

    # Process earliest first, and never mark unattempted/failed webhook IDs.
    chosen = new_streams[:MAX_TO_SEND]
    if chosen:
        delivered = send_to_discord(chosen, run_start=run_start,
                                    seen_ids=already_delivered)
        for stream in delivered:
            if stream['video_id'] not in sent_ids:
                sent_ids.append(stream['video_id'])
        save_state(sent_ids)
        print(f"📌 {len(delivered)} new live alerts recorded")
        return delivered
    save_state(sent_ids)
    print("ℹ️ No new live streams – state unchanged")
    print("\n🎉 All done!")
    return []


if __name__ == "__main__":
    run()
