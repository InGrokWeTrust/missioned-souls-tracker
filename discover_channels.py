import os
import json
from datetime import datetime, timezone
from googleapiclient.discovery import build
from channel_exclusions import is_excluded_channel

API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = (os.environ.get("CHANNEL_NAME") or "Missioned Souls")
TRACKED_CHANNELS_FILE = (os.environ.get("TRACKED_CHANNELS_FILE") or "tracked_channels.json")
CHANNEL_INACTIVITY_DAYS = int(os.environ.get("CHANNEL_INACTIVITY_DAYS", "60"))
DISCOVERY_PAGES = max(1, min(5, int(os.environ.get("DISCOVERY_PAGES") or "2")))
MAX_TRACKED_CHANNELS = int(os.environ.get("MAX_TRACKED_CHANNELS") or "155")

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

    discovered = {}
    page_token = None
    for page in range(DISCOVERY_PAGES):
        arguments = {
            "part": "snippet", "q": f'"{CHANNEL_NAME}"',
            "type": "video", "maxResults": 50, "order": "date"
        }
        if page_token:
            arguments["pageToken"] = page_token
        response = youtube.search().list(**arguments).execute()
        items = response.get("items", [])
        print(f"  Page {page + 1}: found {len(items)} candidate videos")
        for item in items:
            snippet = item.get("snippet", {})
            channel_id = snippet.get("channelId")
            channel_title = snippet.get("channelTitle", "")
            if (channel_id and channel_title and not is_excluded_channel(channel_id)
                    and CHANNEL_NAME.lower() in snippet.get("title", "").lower()):
                discovered[channel_id] = channel_title
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    print(f"✅ Discovered {len(discovered)} unique channels from {page + 1} page(s)")
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
        if is_excluded_channel(channel_id):
            if channel_id in tracked:
                del tracked[channel_id]
            skipped_count += 1
            print(f"  🚫 Excluded channel ID: {channel_id}")
            continue
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
            if len(tracked) >= MAX_TRACKED_CHANNELS:
                print(f"  ⚠️ Capacity limit {MAX_TRACKED_CHANNELS} reached, skipping {channel_title}")
                continue
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
        if is_excluded_channel(cid):
            already_tracked_excluded.append((cid, info.get("title", cid) if isinstance(info,dict) else str(info)))
            continue
        if not isinstance(info, dict):
            continue
        title = info.get("title", "")
        if not should_track_channel(title):
            already_tracked_excluded.append((cid, title))

    for cid, title in already_tracked_excluded:
        print(f"  🗑️  Pruning previously-tracked excluded channel: {title} ({cid})")
        del tracked[cid]
        skipped_count += 1

    # Discovery results can omit channels that still upload regularly.
    # Avoid deleting useful reactors because the search window is crowded.
    now = datetime.now(timezone.utc)
    dormant_count = 0
    for cid, info in tracked.items():
        if not isinstance(info, dict):
            continue
        last_seen_str = info.get("last_seen_at")
        if not last_seen_str:
            continue
        last_seen = datetime.fromisoformat(last_seen_str.replace('Z', '+00:00'))
        days_missing_from_search = (now - last_seen).days
        info["dormant"] = days_missing_from_search > CHANNEL_INACTIVITY_DAYS
        if info["dormant"]:
            dormant_count += 1
    print(f"🛡️ {dormant_count} channels marked dormant in search results (still tracked).")

    save_tracked_channels(tracked)

    print(f"\n✅ Discovery complete")
    print(f"   New channels added: {new_count}")
    print(f"   Excluded/skipped channels: {skipped_count}")
    print(f"   Search-dormant channels retained: {dormant_count}")
    print(f"   Total tracked: {len(tracked)}")
    print("\n🎉 All done!")
