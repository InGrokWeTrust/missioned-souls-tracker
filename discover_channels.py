import os
import json
from datetime import datetime, timezone
from googleapiclient.discovery import build

API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
TRACKED_CHANNELS_FILE = os.environ.get("TRACKED_CHANNELS_FILE", "tracked_channels.json")
CHANNEL_INACTIVITY_DAYS = int(os.environ.get("CHANNEL_INACTIVITY_DAYS", "60"))

# Exclusion rule: any channel with these keywords is skipped,
# unless it also matches an allowed exception.
EXCLUDED_CHANNEL_KEYWORDS = ["vlog"]
ALLOWED_CHANNEL_EXCEPTIONS = ["cherman"]

youtube = build('youtube', 'v3', developerKey=API_KEY)


def should_track_channel(channel_title):
    """Return True if the channel should be tracked."""
    title_lower = channel_title.lower()

    # If it matches an allowed exception, always track
    if any(kw in title_lower for kw in ALLOWED_CHANNEL_EXCEPTIONS):
        return True

    # Otherwise, exclude if it matches any exclusion keyword
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


def save_tracked_channels(channels):
    data = {
        "channels": channels,
        "last_discovery_run": datetime.now(timezone.utc).isoformat()
    }
    with open(TRACKED_CHANNELS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"💾 Saved {len(channels)} tracked channels to {TRACKED_CHANNELS_FILE}")


def discover_channels():
    print(f"🔍 Discovering new reactor channels for {CHANNEL_NAME}...\n")

    search_response = youtube.search().list(
        part="snippet",
        q=f'"{CHANNEL_NAME}" (reacts OR reaction OR "first time" OR "react to" OR reacting)',
        type="video",
        maxResults=50,
        order="date"
    ).execute()

    items = search_response.get('items', [])
    print(f"Search returned {len(items)} videos\n")

    discovered = {}
    for item in items:
        snippet = item['snippet']
        channel_id = snippet['channelId']
        channel_title = snippet['channelTitle']
        discovered[channel_id] = channel_title

    return discovered


if __name__ == "__main__":
    print("🚀 Channel Discovery Started\n")

    tracked, last_run = load_tracked_channels()
    print(f"📋 Currently tracking {len(tracked)} channels")
    if last_run:
        print(f"📅 Last discovery: {last_run}")

    discovered = discover_channels()

    new_count = 0
    skipped_count = 0
    for channel_id, channel_title in discovered.items():
        # Apply exclusion rule first
        if not should_track_channel(channel_title):
            if channel_id in tracked:
                print(f"  🗑️  Removing excluded channel: {channel_title} ({channel_id})")
                del tracked[channel_id]
                skipped_count += 1
            else:
                print(f"  🚫 Skipping excluded channel: {channel_title}")
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
            if isinstance(tracked[channel_id], str):
                tracked[channel_id] = {"title": channel_title, "added_at": datetime.now(timezone.utc).isoformat()}
            tracked[channel_id]["title"] = channel_title
            tracked[channel_id]["last_seen_at"] = datetime.now(timezone.utc).isoformat()

    # Also prune any already-tracked excluded channels (in case rule changed)
    already_tracked_excluded = []
    for cid, info in list(tracked.items()):
        if not isinstance(info, dict):
            continue
        title = info.get("title", "")
        if not should_track_channel(title):
            already_tracked_excluded.append((cid, title))

    for cid, title in already_tracked_excluded:
        print(f"  🗑️  Pruning previously-tracked excluded channel: {title} ({cid})")
        del tracked[cid]
        skipped_count += 1

    # Prune stale channels
    now = datetime.now(timezone.utc)
    to_remove = []
    for cid, info in tracked.items():
        if isinstance(info, str):
            continue
        last_seen_str = info.get("last_seen_at")
        if not last_seen_str:
            continue
        last_seen = datetime.fromisoformat(last_seen_str.replace('Z', '+00:00'))
        days_idle = (now - last_seen).days
        if days_idle > CHANNEL_INACTIVITY_DAYS:
            to_remove.append(cid)

    for cid in to_remove:
        title = tracked[cid].get("title", cid) if isinstance(tracked[cid], dict) else tracked[cid]
        print(f"  🗑️  Pruning stale channel: {title} ({cid})")
        del tracked[cid]

    save_tracked_channels(tracked)

    print(f"\n✅ Discovery complete")
    print(f"   New channels added: {new_count}")
    print(f"   Excluded/skipped channels: {skipped_count}")
    print(f"   Stale channels pruned: {len(to_remove)}")
    print(f"   Total tracked: {len(tracked)}")
    print("\n🎉 All done!")
