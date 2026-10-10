import os
import json
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build

API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
TRACKED_CHANNELS_FILE = os.environ.get("TRACKED_CHANNELS_FILE", "tracked_channels.json")
CHANNEL_INACTIVITY_DAYS = int(os.environ.get("CHANNEL_INACTIVITY_DAYS", "60"))

# Mode control
DISCOVERY_MODE = os.environ.get("DISCOVERY_MODE", "normal").lower()  # "normal" or "deep"

# Configurable limits
DAYS_LOOKBACK = int(os.environ.get("DAYS_LOOKBACK", "21"))  # 3 weeks lookback for deep

EXCLUDED_CHANNEL_KEYWORDS = ["vlog"]
ALLOWED_CHANNEL_EXCEPTIONS = ["cherman"]

youtube = build('youtube', 'v3', developerKey=API_KEY)


def should_track_channel(channel_title: str) -> bool:
    title_lower = channel_title.lower()
    if any(kw in title_lower for kw in ALLOWED_CHANNEL_EXCEPTIONS):
        return True
    if any(kw in title_lower for kw in EXCLUDED_CHANNEL_KEYWORDS):
        return False
    return True


def load_tracked_channels():
    try:
        with open(TRACKED_CHANNELS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('channels', {}), data.get('last_discovery_run')
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, None


def save_tracked_channels(channels: dict):
    data = {
        "channels": channels,
        "last_discovery_run": datetime.now(timezone.utc).isoformat()
    }
    with open(TRACKED_CHANNELS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"💾 Saved {len(channels)} tracked channels to {TRACKED_CHANNELS_FILE}")


def search_videos(order: str = "date", max_pages: int = 1) -> dict:
    """Run search.list with pagination and return {channel_id: channel_title}"""
    discovered = {}
    page_token = None
    published_after = (datetime.now(timezone.utc) - timedelta(days=DAYS_LOOKBACK)).isoformat()

    for page in range(max_pages):
        request = youtube.search().list(
            part="snippet",
            q=f'"{CHANNEL_NAME}"',
            type="video",
            maxResults=50,
            order=order,
            publishedAfter=published_after,
            pageToken=page_token
        )
        response = request.execute()

        items = response.get('items', [])
        for item in items:
            snippet = item['snippet']
            channel_id = snippet['channelId']
            channel_title = snippet['channelTitle']
            discovered[channel_id] = channel_title

        print(f"  → Page {page + 1}/{max_pages} ({order}) - {len(items)} videos")

        page_token = response.get('nextPageToken')
        if not page_token:
            print("  → No more pages available")
            break

    return discovered


def discover_channels() -> dict:
    print(f"🔍 Discovery mode: {DISCOVERY_MODE.upper()}")
    print(f"   Lookback period: {DAYS_LOOKBACK} days\n")

    if DISCOVERY_MODE == "deep":
        # Aggressive weekly scan - 20 pages
        print("Running DEEP discovery (20 pages)...")
        discovered = search_videos(order="date", max_pages=20)
    else:
        # Normal light discovery
        print("Running NORMAL discovery...")
        discovered = search_videos(order="date", max_pages=2)

        # Optional second search for better coverage
        extra = search_videos(order="viewCount", max_pages=1)
        before = len(discovered)
        discovered.update(extra)
        print(f"  Secondary search added {len(discovered) - before} extra channels")

    print(f"\nTotal unique channels found this run: {len(discovered)}")
    return discovered


if __name__ == "__main__":
    print("🚀 Channel Discovery Started\n")

    tracked, last_run = load_tracked_channels()
    print(f"📋 Currently tracking {len(tracked)} channels")
    if last_run:
        print(f"📅 Last discovery: {last_run}\n")

    discovered = discover_channels()

    new_count = 0
    skipped_count = 0

    for channel_id, channel_title in discovered.items():
        if not should_track_channel(channel_title):
            if channel_id in tracked:
                print(f"  🗑️  Removing excluded: {channel_title}")
                del tracked[channel_id]
            else:
                print(f"  🚫 Skipping excluded: {channel_title}")
            skipped_count += 1
            continue

        if channel_id not in tracked:
            tracked[channel_id] = {
                "title": channel_title,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "last_seen_at": datetime.now(timezone.utc).isoformat()
            }
            new_count += 1
            print(f"  ➕ NEW: {channel_title} ({channel_id})")
        else:
            # Update existing entry
            if isinstance(tracked[channel_id], str):
                tracked[channel_id] = {
                    "title": channel_title,
                    "added_at": datetime.now(timezone.utc).isoformat()
                }
            tracked[channel_id]["title"] = channel_title
            tracked[channel_id]["last_seen_at"] = datetime.now(timezone.utc).isoformat()

    # Prune any already-tracked excluded channels
    for cid, info in list(tracked.items()):
        if isinstance(info, dict) and not should_track_channel(info.get("title", "")):
            print(f"  🗑️  Pruning excluded: {info.get('title')}")
            del tracked[cid]
            skipped_count += 1

    # Prune stale channels
    now = datetime.now(timezone.utc)
    to_remove = []
    for cid, info in tracked.items():
        if not isinstance(info, dict):
            continue
        last_seen_str = info.get("last_seen_at")
        if not last_seen_str:
            continue
        last_seen = datetime.fromisoformat(last_seen_str.replace('Z', '+00:00'))
        if (now - last_seen).days > CHANNEL_INACTIVITY_DAYS:
            to_remove.append(cid)

    for cid in to_remove:
        title = tracked[cid].get("title", cid)
        print(f"  🗑️  Pruning stale: {title}")
        del tracked[cid]

    save_tracked_channels(tracked)

    units_used = 20 * 100 if DISCOVERY_MODE == "deep" else 300
    print(f"\n✅ Discovery complete")
    print(f"   New channels added : {new_count}")
    print(f"   Excluded/skipped   : {skipped_count}")
    print(f"   Stale pruned       : {len(to_remove)}")
    print(f"   Total tracked      : {len(tracked)}")
    print(f"   Approx. quota used : ~{units_used} units")
    print("\n🎉 All done!")
