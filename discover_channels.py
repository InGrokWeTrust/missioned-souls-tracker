import os
import json
from datetime import datetime, timezone
from googleapiclient.discovery import build
from channel_exclusions import is_excluded_channel
from discovery_video_handoff import add_candidates, active_candidates

API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
TRACKED_CHANNELS_FILE = os.environ.get("TRACKED_CHANNELS_FILE", "tracked_channels.json")
CHANNEL_INACTIVITY_DAYS = int(os.environ.get("CHANNEL_INACTIVITY_DAYS", "60"))
# At most one additional search.list request (1 Search Queries unit) per run.
# DISCOVERY_PAGES=1 is a rollback switch if daily search quota becomes tight.
DISCOVERY_PAGES = max(1, min(2, int(os.environ.get("DISCOVERY_PAGES") or "2")))

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


def discover_channels(*, include_videos=False):
    """Search up to two pages using the ORIGINAL broad band-name query.

    Do not narrow discovery using reaction keywords or metadata classifiers.
    Record precise qualifying video IDs for the existing new-channel handoff.
    """
    print(f"🔍 Discovering new reactor channels for {CHANNEL_NAME}...\n")

    discovered = {}
    source_videos = {}
    seen_source_ids = set()
    page_token = None
    api_calls = 0
    results_scanned = 0

    for page in range(DISCOVERY_PAGES):
        # First page has no pageToken; the second requires YouTube's token.
        arguments = {
            "part": "snippet",
            "q": f'"{CHANNEL_NAME}"',
            "type": "video",
            "maxResults": 50,
            "order": "date",
        }
        if page_token:
            arguments["pageToken"] = page_token
        response = youtube.search().list(**arguments).execute()
        api_calls += 1
        items = response.get("items", [])
        results_scanned += len(items)
        print(f"  Page {page + 1}: {len(items)} search results")

        for item in items:
            snippet = item.get('snippet', {})
            channel_id = snippet.get('channelId')
            channel_title = snippet.get('channelTitle', '')
            if not channel_id or not channel_title:
                continue
            if is_excluded_channel(channel_id):
                print(f"  🚫 Skipping excluded channel ID: {channel_id}")
                continue
            discovered[channel_id] = channel_title
            # The unchanged broad-title condition applies to source videos,
            # NOT to deciding whether a channel may be discovered.
            if CHANNEL_NAME.lower() in snippet.get('title', '').lower():
                video_id = (item.get('id') or {}).get('videoId')
                key = (channel_id, video_id)
                if (video_id and snippet.get('publishedAt')
                        and key not in seen_source_ids):
                    seen_source_ids.add(key)
                    source_videos.setdefault(channel_id, []).append({
                        'video_id': video_id,
                        'published_at': snippet['publishedAt'],
                    })

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    print(f"📊 Discovery searched {api_calls} page(s), "
          f"{results_scanned} results, {len(discovered)} unique channels")
    return (discovered, source_videos) if include_videos else discovered

def mark_search_dormancy(tracked, *, now=None):
    """Annotate search absence without removing tracked upload sources.

    last_seen_at is the most recent Discovery search appearance, not the
    channel's latest upload. A channel missing from the rolling search
    results must remain available to the independent upload tracker.
    """
    now = now or datetime.now(timezone.utc)
    dormant_count = 0
    for info in tracked.values():
        if not isinstance(info, dict):
            continue
        last_seen_str = info.get("last_seen_at")
        if not last_seen_str:
            continue
        try:
            last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
        except (AttributeError, TypeError, ValueError):
            # Unknown/legacy dates cannot justify deleting a reactor.
            print("  ⚠️  Invalid last-seen timestamp; preserving channel")
            continue
        info["dormant"] = (now - last_seen).days > CHANNEL_INACTIVITY_DAYS
        if info["dormant"]:
            dormant_count += 1
    print(f"🛡️ {dormant_count} channels absent from recent searches (still tracked).")
    return dormant_count


def run_discovery():
    print("🚀 Channel Discovery Started\n")

    tracked, last_run = load_tracked_channels()
    print(f"📋 Currently tracking {len(tracked)} channels")
    if last_run:
        print(f"📅 Last discovery: {last_run}")

    discovered, source_videos = discover_channels(include_videos=True)

    new_count = 0
    handoff_added = 0
    skipped_count = 0
    for channel_id, channel_title in discovered.items():
        # Apply exclusion rule first
        if is_excluded_channel(channel_id) or not should_track_channel(channel_title):
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
            handoff_added += add_candidates(
                tracked[channel_id], source_videos.get(channel_id),
                now=datetime.now(timezone.utc))
            print(f"  ➕ NEW: {channel_title} ({channel_id})")
        else:
            if isinstance(tracked[channel_id], str):
                tracked[channel_id] = {"title": channel_title, "added_at": datetime.now(timezone.utc).isoformat()}
            tracked[channel_id]["title"] = channel_title
            tracked[channel_id]["last_seen_at"] = datetime.now(timezone.utc).isoformat()
            # Complete a recent first-discovery handoff, without backfilling
            # arbitrary older channels or rewriting their registration time.
            handoff_added += add_candidates(
                tracked[channel_id], source_videos.get(channel_id),
                now=datetime.now(timezone.utc))

    # Expire handoff IDs automatically; these are short-lived scan hints.
    for info in tracked.values():
        if isinstance(info, dict) and 'discovery_videos' in info:
            active = active_candidates(info)
            if active:
                info['discovery_videos'] = active
            else:
                del info['discovery_videos']

    # Also prune any already-tracked excluded channels (in case rule changed)
    already_tracked_excluded = []
    for cid, info in list(tracked.items()):
        if not isinstance(info, dict):
            continue
        title = info.get("title", "")
        if is_excluded_channel(cid) or not should_track_channel(title):
            already_tracked_excluded.append((cid, title))

    for cid, title in already_tracked_excluded:
        print(f"  🗑️  Pruning previously-tracked excluded channel: {title} ({cid})")
        del tracked[cid]
        skipped_count += 1

    # A crowded or changing search window is not evidence of inactivity.
    # Retain all established channels so their uploads remain scanned.
    dormant_count = mark_search_dormancy(tracked)
    save_tracked_channels(tracked)

    print(f"\n✅ Discovery complete")
    print(f"   New channels added: {new_count}")
    print(f"   Discovery source video IDs saved: {handoff_added}")
    print(f"   Excluded/skipped channels: {skipped_count}")
    print(f"   Search-dormant channels retained: {dormant_count}")
    print(f"   Total tracked: {len(tracked)}")
    print("\n🎉 All done!")


if __name__ == "__main__":
    run_discovery()
