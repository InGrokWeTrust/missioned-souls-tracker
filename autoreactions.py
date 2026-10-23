import os
import time
import json
import re
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from notification_history import read_ids, save_ids
from channel_exclusions import is_excluded_channel
from discovery_video_handoff import handoff_for_scan
from thumbnail_policy import select_thumbnail, valid_thumbnail, require_thumbnails
from video_release_policy import is_published_vod

# ================== READ CONFIG FROM ENVIRONMENT ==================
API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    raise ValueError("Missing YOUTUBE_API_KEY environment variable")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Missioned Souls")
MAX_TO_SEND = int(os.environ.get("MAX_TO_SEND", "10"))
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "5"))
FORCE_SEND_ALL = os.environ.get("FORCE_SEND_ALL", "false").lower() == "true"
LAST_RUN_FILE = os.environ.get("LAST_RUN_FILE", "last_run.json")
TRACKED_CHANNELS_FILE = os.environ.get("TRACKED_CHANNELS_FILE", "tracked_channels.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

# ===========================================

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")

youtube = build('youtube', 'v3', developerKey=API_KEY)


# The legacy timestamp remains the cutoff. Only uploads held for
# publication/thumbnail availability require a small persistent retry record.
MAX_HELD_VODS = 200


def _read_bookmark():
    try:
        with open(LAST_RUN_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Invalid last-run bookmark (expected object)")
    value = data.get('last_published_at')
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError("Invalid last_published_at in bookmark")
    return data


def load_last_run():
    return _read_bookmark().get('last_published_at')


def load_held_videos():
    """Read only explicitly deferred uploads, never inferred old reactions."""
    values = _read_bookmark().get('held_videos', [])
    if not isinstance(values, list) or len(values) > MAX_HELD_VODS:
        raise ValueError("Invalid held-video bookmark")
    out = []
    seen = set()
    for entry in values:
        if not isinstance(entry, dict):
            raise ValueError("Invalid held-video record")
        cid, vid, stamp = (entry.get('channel_id'), entry.get('video_id'),
                           entry.get('published_at'))
        if not all(isinstance(v, str) and v for v in (cid, vid, stamp)):
            raise ValueError("Invalid held-video identity")
        if vid not in seen:
            out.append({'channel_id': cid, 'video_id': vid,
                        'published_at': stamp})
            seen.add(vid)
    return out


def _save_bookmark(data):
    """Atomic bookmark update; never lose held IDs when timestamp advances."""
    temporary = LAST_RUN_FILE + ".tmp"
    try:
        with open(temporary, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, LAST_RUN_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_last_run(published_at):
    """Save original timestamp while retaining deferred releases."""
    data = _read_bookmark()
    data['last_published_at'] = published_at
    _save_bookmark(data)
    print(f"💾 Updated {LAST_RUN_FILE} → {published_at[:10]}")


def save_held_videos(held):
    """Hold only unpublished or missing-thumbnail videos for retry."""
    if len(held) > MAX_HELD_VODS:
        raise RuntimeError("Held-video limit reached; refusing to lose an upload")
    data = _read_bookmark()
    data['held_videos'] = list(held.values())
    _save_bookmark(data)
    print(f"💾 Retained {len(held)} waiting VODs for publication/thumbnail retry")


def load_tracked_channels():
    try:
        with open(TRACKED_CHANNELS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            channels = data.get('channels', {})
            # Defense in depth: even an older tracked_channels.json cannot
            # permit an excluded uploader to reach Discord.
            return {cid: info for cid, info in channels.items()
                    if not is_excluded_channel(cid)}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def parse_duration(duration_str):
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


def is_missioned_souls_reaction(title):
    """Return True if the title mentions Missioned Souls (case-insensitive)."""
    return CHANNEL_NAME.lower() in title.lower()


def is_archived_livestream(video):
    """Return True if the video has livestream details (active, upcoming, or archived)."""
    return video.get('live_streaming_details') is not None


def is_short_or_too_short(video):
    title_lower = video.get('title', '').lower()

    # 1. Prefer explicit Shorts labels; generic words in unrelated titles should not match.
    if re.search(r"#shorts?\b|\bshorts\b", title_lower):
        return True

    # 2. Keep ALL livestreams — active, upcoming, or archived
    if is_archived_livestream(video):
        return False

    # 3. Keep live/upcoming detected via snippet liveBroadcastContent
    if video.get('live_broadcast_content') in ('live', 'upcoming'):
        return False

    duration = video.get('duration_sec', 0)

    # 4. Keep videos with unknown duration (safer than dropping them)
    if duration == 0:
        return False

    # 5. Filter confirmed short videos
    return duration < 120


def classify_filter_reason(video):
    """Return a short reason string for why a video was filtered."""
    title_lower = video.get('title', '').lower()

    if re.search(r"#shorts?\b|\bshorts\b", title_lower):
        return "marked as Short"

    duration = video.get('duration_sec', 0)
    if duration > 0 and duration < 120:
        return f"too short ({duration}s)"

    return "unknown"


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
    """Return the absolute publish time in Pacific/Auckland timezone (e.g. '6:18 PM')."""
    try:
        ts = published_at_iso.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Convert to Pacific/Auckland
        local_dt = dt.astimezone(AUCKLAND_TZ)
        return local_dt.strftime('%I:%M %p %Z').lstrip('0')
    except Exception:
        return ""


def fetch_channel_videos(channel_id, channel_title):
    """Fetch the newest N videos from a channel's uploads playlist."""
    uploads_playlist_id = "UU" + channel_id[2:]

    try:
        response = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=MAX_VIDEOS_PER_CHANNEL
        ).execute()
    except Exception as e:
        print(f"  ❌ Error fetching {channel_title}: {e}")
        return []

    videos = []
    for item in response.get('items', []):
        snippet = item['snippet']
        content = item.get('contentDetails', {})
        video_id = content.get('videoId') or snippet.get('resourceId', {}).get('videoId')
        if not video_id:
            continue

        videos.append({
            'title': snippet['title'],
            'video_id': video_id,
            'channel': channel_title,
            'channel_id': channel_id,
            'published_at': content.get('videoPublishedAt') or snippet.get('publishedAt'),
            'url': f"https://youtu.be/{video_id}",
            'thumbnail': snippet.get('thumbnails', {}).get('medium', {}).get('url'),
            'live_broadcast_content': snippet.get('liveBroadcastContent', 'none'),
            'live_streaming_details': None  # filled in later
        })

    return videos


def get_reactions_with_stats(*, held=None):
    run_start = datetime.now(timezone.utc)
    print(f"🔍 Checking {CHANNEL_NAME} reactions from tracked channels...")
    print(f"⏰ Run started at: {run_start.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    tracked = load_tracked_channels()
    if not tracked:
        print("⚠️ No tracked channels found. Run discover_channels.py first.")
        return [], run_start

    print(f"📋 Tracking {len(tracked)} channels\n")

    # Stage 1: fetch all candidate videos
    candidates = []
    no_ms_count = 0
    silent_channels = []

    for channel_id, info in tracked.items():
        channel_title = info.get('title', channel_id) if isinstance(info, dict) else str(info)

        videos = fetch_channel_videos(channel_id, channel_title)
        reaction_videos = [v for v in videos if is_missioned_souls_reaction(v['title'])]
        no_ms_count += len(videos) - len(reaction_videos)

        for v in reaction_videos:
            candidates.append(v)

        if not reaction_videos:
            silent_channels.append(channel_title)

    # Preserve the original 5-upload scan. Discovery and deferred releases
    # contribute exact IDs that can have fallen outside that window.
    normal_matches = len(candidates)
    scanned_count = normal_matches + no_ms_count
    by_id = {v['video_id']: v for v in candidates}
    source_ids = set()
    for cid, vid, published in handoff_for_scan(tracked, now=run_start):
        if cid not in tracked or is_excluded_channel(cid):
            continue
        source_ids.add(vid)
        if vid in by_id:
            by_id[vid]['discovery_handoff'] = True
            continue
        info = tracked[cid]
        entry = {
            'video_id': vid, 'channel_id': cid,
            'channel': info.get('title', cid) if isinstance(info, dict) else str(info),
            'title': '', 'published_at': published,
            'url': f"https://youtu.be/{vid}", 'thumbnail': None,
            'live_broadcast_content': 'none', 'live_streaming_details': None,
            'discovery_handoff': True,
        }
        candidates.append(entry)
        by_id[vid] = entry

    for entry in held or []:
        cid, vid = entry['channel_id'], entry['video_id']
        if cid not in tracked or is_excluded_channel(cid):
            continue
        if vid in by_id:
            by_id[vid]['deferred'] = True
            continue
        info = tracked[cid]
        record = {
            'video_id': vid, 'channel_id': cid,
            'channel': info.get('title', cid) if isinstance(info, dict) else str(info),
            'title': '', 'published_at': entry['published_at'],
            'url': f"https://youtu.be/{vid}", 'thumbnail': None,
            'live_broadcast_content': 'none', 'live_streaming_details': None,
            'deferred': True,
        }
        candidates.append(record)
        by_id[vid] = record
    if source_ids:
        print(f"🔗 Discovery video handoff: {len(source_ids)} source IDs to verify")
    if held:
        print(f"📦 Deferred VOD rechecks: {len(held)} held IDs")

    if silent_channels:
        print(f"⏭️  Channels with no MS reactions in last {MAX_VIDEOS_PER_CHANNEL}:")
        for ch in silent_channels:
            print(f"     • {ch}")
        print()

    print(f"📊 Stage 1 — Channel scan:")
    print(f"   Channels checked         : {len(tracked)}")
    print(f"   Videos scanned           : {scanned_count}")
    print(f"   MS keyword matches       : {normal_matches}")
    print(f"   No MS mention (skipped)  : {no_ms_count}\n")

    if not candidates:
        print("⚠️ No Missioned Souls reactions found in tracked channels.")
        return [], run_start

    # Stage 2: batch fetch stats + duration + liveStreamingDetails
    video_ids = [v['video_id'] for v in candidates]
    stats_dict = {}
    duration_dict = {}
    live_details_dict = {}
    details_dict = {}

    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        stats_response = youtube.videos().list(
            part="snippet,statistics,contentDetails,liveStreamingDetails,status",
            id=",".join(chunk)
        ).execute()

        for item in stats_response.get('items', []):
            vid_id = item['id']
            details_dict[vid_id] = item
            stats = item.get('statistics', {})
            stats_dict[vid_id] = {
                'view_count': int(stats.get('viewCount', 0)),
                'like_count': int(stats.get('likeCount', 0)),
                'comment_count': int(stats.get('commentCount', 0))
            }
            content_details = item.get('contentDetails', {})
            duration_str = content_details.get('duration', 'PT0S')
            duration_dict[vid_id] = parse_duration(duration_str)

            if 'liveStreamingDetails' in item:
                live_details_dict[vid_id] = item['liveStreamingDetails']

    # Stage 3: apply filters, log per-video outcome
    all_videos = []
    filtered_log = []

    for video in candidates:
        vid_id = video['video_id']
        detail = details_dict.get(vid_id, {})
        snippet = detail.get('snippet') or {}
        privacy = (detail.get('status') or {}).get('privacyStatus')
        confirmed = (snippet.get('channelId') == video['channel_id']
                     and privacy == 'public'
                     and isinstance(snippet.get('title'), str)
                     and bool(snippet.get('publishedAt')))
        # Independent confirmation of the specific source uploader is mandatory
        # for Discovery IDs and older held videos. Never trust placeholders.
        if not confirmed and (video.get('discovery_handoff') or video.get('deferred')):
            print(f"⏭️ Exact-ID candidate {vid_id} not publicly verified; retry later")
            continue
        if confirmed:
            video['title'] = snippet['title']
            video['published_at'] = snippet['publishedAt']
            video['channel'] = snippet.get('channelTitle') or video['channel']
            video['live_broadcast_content'] = snippet.get(
                'liveBroadcastContent', video.get('live_broadcast_content', 'none'))
            video['thumbnail'] = select_thumbnail(
                snippet.get('thumbnails'), vid_id, fallback=video.get('thumbnail'))
        else:
            video['thumbnail'] = select_thumbnail(
                None, vid_id, fallback=video.get('thumbnail'))
        video['metadata_confirmed'] = confirmed
        # The original title-only criterion applies to every ID, including
        # newly discovered and deferred records. No strict classifier.
        if not is_missioned_souls_reaction(video.get('title', '')):
            continue
        vid_stats = stats_dict.get(vid_id, {})
        video['view_count'] = vid_stats.get('view_count', 0)
        video['like_count'] = vid_stats.get('like_count', 0)
        video['comment_count'] = vid_stats.get('comment_count', 0)
        video['duration_sec'] = duration_dict.get(vid_id, 0)
        video['live_streaming_details'] = live_details_dict.get(vid_id)
        video['age_at_run'] = humanize_ago(video['published_at'], run_start)

        if is_short_or_too_short(video):
            reason = classify_filter_reason(video)
            filtered_log.append((video, reason))
        else:
            all_videos.append(video)

    if filtered_log:
        print(f"🚫 Stage 2 — Filtered out ({len(filtered_log)} videos):")
        for v, reason in filtered_log:
            ch = v['channel'][:24]
            print(f"   ⏭️  [{reason}] {ch:24} | {v['title'][:55]}")
        print()

    all_videos.sort(key=lambda x: x['published_at'], reverse=False)

    print(f"📊 Stage 3 — Final:")
    print(f"   Candidates               : {len(candidates)}")
    print(f"   Filtered (shorts/other)  : {len(filtered_log)}")
    print(f"   ✅ Kept for posting      : {len(all_videos)}\n")

    return all_videos, run_start


def send_to_discord(videos, max_to_send=5, run_start=None, seen_ids=None):
    """Send and durably record each accepted webhook, before the next send."""
    # Final protection even if callers bypass the normal channel scan.
    videos = [v for v in videos if not is_excluded_channel(v.get('channel_id'))]
    if not videos:
        print("⚠️ No new videos to send.")
        return []
    # Final safety net for direct callers: no premature broadcast alerts
    # and never construct a Discord card with a missing/invalid image.
    for video in videos[:max_to_send]:
        if not is_published_vod(video) or is_short_or_too_short(video):
            raise RuntimeError("Unpublished/live/Short video cannot be delivered as a VOD")
    require_thumbnails(videos[:max_to_send])
    if seen_ids is None:
        seen_ids = set()
    delivered = []

    print(f"\n📨 Sending {min(max_to_send, len(videos))} new reactions to Discord...\n")

    for video in videos[:max_to_send]:
        vid = video['video_id']
        if vid in seen_ids:
            print(f"⏭️ Already delivered: {vid}")
            continue
        title = video['title']
        if video.get('live_broadcast_content') == 'live':
            title = f"🔴 LIVE: {title}"
        elif video.get('live_broadcast_content') == 'upcoming':
            title = f"🕒 UPCOMING: {title}"

        age = video.get('age_at_run', humanize_ago(video['published_at'], run_start))
        published_str = format_published_time(video['published_at'])
        posted_value = f"{published_str} ({age})" if published_str else age

        embed = {
            "title": title,
            "url": video['url'],
            "color": 0xe53935 if video.get('live_broadcast_content') == 'live' else 0x1e88e5,
            "image": {"url": video['thumbnail']},
            "fields": [
                {"name": "Channel", "value": video['channel'], "inline": True},
                {"name": "Views", "value": f"{video.get('view_count', 0):,}", "inline": True},
                {"name": "Posted", "value": posted_value, "inline": True},
                {"name": "Likes", "value": f"{video.get('like_count', 0):,}", "inline": True},
                {"name": "Comment", "value": f"{video.get('comment_count', 0):,}", "inline": True},
            ],
            "timestamp": video['published_at']
        }

        data = {
            "username": "Missioned Souls Reactions",
            "embeds": [embed]
        }

        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
        except Exception as e:
            raise RuntimeError(f"Discord delivery failed for {vid}; bookmark unchanged") from e
        if response.status_code != 204:
            raise RuntimeError(f"Discord HTTP {response.status_code} for {vid}; bookmark unchanged")
        # Durable history is the primary dedup source. If it cannot be written,
        # stop immediately: never mark this notification as fully acknowledged.
        save_ids("reactions", [vid])
        seen_ids.add(vid)
        delivered.append(video)
        print(f"✅ Sent: [{posted_value}] {title[:60]}...")
        time.sleep(1.3)
    return delivered


# ===================== MAIN =====================
def run():
    print("🚀 Missioned Souls Reaction Tracker Started\n")
    # Fail closed before YouTube/Discord if delivery ledgers are unavailable.
    reaction_ids = read_ids("reactions")
    live_ids = read_ids("live")
    already_delivered = reaction_ids | live_ids
    print(f"📋 Durable delivered history: {len(reaction_ids)} reactions, "
          f"{len(live_ids)} live IDs")

    last_published = load_last_run()
    original_held = load_held_videos()
    # An old bookmark may contain newly excluded or already-delivered IDs.
    held = {v['video_id']: v for v in original_held
            if not is_excluded_channel(v['channel_id'])
            and v['video_id'] not in already_delivered}
    print(f"📅 Last run timestamp: {last_published[:10] if last_published else 'No bookmark — will be set this run'}")
    videos, run_start = get_reactions_with_stats(held=list(held.values()))
    videos = [v for v in videos if not is_excluded_channel(v.get('channel_id'))]

    if not last_published:
        # Original bootstrap policy: no historical upload is resent.
        if videos:
            newest = max(v['published_at'] for v in videos)
            save_last_run(newest)
            print(f"📌 Initial bookmark set without Discord delivery: {newest}")
        if len(held) != len(original_held):
            save_held_videos(held)
        print("\n🎉 All done!")
        return []

    eligible = []
    seen_in_scan = set()
    for video in videos:
        vid = video['video_id']
        if vid in already_delivered or vid in seen_in_scan:
            continue
        seen_in_scan.add(vid)
        # Source video-ID handoff is independently verified in videos.list
        # and may predate the original timestamp when its channel was added.
        allowed = (FORCE_SEND_ALL or video['published_at'] > last_published
                   or video.get('discovery_handoff') or vid in held)
        if not allowed:
            continue

        if not is_published_vod(video):
            held[vid] = {'channel_id': video['channel_id'], 'video_id': vid,
                         'published_at': video['published_at']}
            print(f"⏳ Waiting for publication/end of stream: {vid}")
            continue
        if not valid_thumbnail(video.get('thumbnail'), vid):
            held[vid] = {'channel_id': video['channel_id'], 'video_id': vid,
                         'published_at': video['published_at']}
            print(f"🖼️ Waiting for an official video thumbnail: {vid}")
            continue
        eligible.append(video)

    eligible.sort(key=lambda v: (v['published_at'], v['video_id']))
    print(f"🆕 {len(eligible)} eligible unseen published reaction VODs")
    print(f"📦 {len(held)} held pending publication/thumbnail")

    # Persist waiting video IDs *before* posting anything; if the video falls
    # outside the newest five uploads next run, it must still be retried.
    if list(held.values()) != original_held:
        save_held_videos(held)

    chosen = eligible[:MAX_TO_SEND]
    if not chosen:
        print("ℹ️ No ready reactions — bookmark unchanged")
        return []

    delivered = send_to_discord(
        chosen, max_to_send=MAX_TO_SEND, run_start=run_start,
        seen_ids=already_delivered)
    # Durable delivered-ID history is updated immediately after each webhook.
    # Remove released held IDs only once delivery is independently confirmed.
    changed = False
    for video in delivered:
        if held.pop(video['video_id'], None) is not None:
            changed = True
    if changed:
        save_held_videos(held)
    if delivered:
        newest = max(v['published_at'] for v in delivered)
        if newest > last_published:
            save_last_run(newest)
        print(f"📌 {len(delivered)} newly sent IDs saved durably")
    print("\n🎉 All done!")
    return delivered


if __name__ == "__main__":
    run()
