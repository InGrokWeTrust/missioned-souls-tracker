"""One-time, read-only YouTube metadata diagnostic for the reported missed reaction.

Uses one videos.list request. Never sends Discord messages or edits state.
Do not print full HTTP exception strings: request URLs contain the API key.
"""
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

from new_channel_reaction_evidence import (
    clear_reaction_title, plausible_reaction_title, subject_reaction_search_hit
)
from video_release_policy import is_published_vod

VIDEO_ID = "xclrgEcb8mo"


def main():
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("Diagnostic unavailable: YouTube API key not supplied.")
        return 3
    params = urlencode({
        "part": "snippet,contentDetails,status,liveStreamingDetails",
        "id": VIDEO_ID, "key": key
    })
    try:
        with urlopen("https://www.googleapis.com/youtube/v3/videos?" + params,
                     timeout=20) as response:
            data = json.load(response)
    except Exception as error:
        print("YouTube metadata request failed:", type(error).__name__)
        return 3
    items = data.get("items", [])
    if len(items) != 1:
        print("Video ID was not available to videos.list; cannot classify.")
        return 2
    item = items[0]
    snippet = item.get("snippet") or {}
    title = snippet.get("title", "")
    description = snippet.get("description", "")
    channel_id = snippet.get("channelId", "")
    privacy = (item.get("status") or {}).get("privacyStatus")
    live = snippet.get("liveBroadcastContent", "none")
    published = snippet.get("publishedAt", "")
    duration = (item.get("contentDetails") or {}).get("duration", "")
    thumbnail = bool((snippet.get("thumbnails") or {}).get("high"))
    evidence = {"title": title, "description": description}
    with open("tracked_channels.json", encoding="utf-8") as f:
        tracked = (json.load(f).get("channels") or {})
    published_age_hours = None
    try:
        published_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        published_age_hours = round(
            (datetime.now(timezone.utc) - published_dt).total_seconds() / 3600, 2
        )
    except ValueError:
        pass
    result = {
        "video_id": VIDEO_ID,
        "title": title,
        "description_opening": description[:500],
        "channel_title": snippet.get("channelTitle"),
        "channel_id": channel_id,
        "already_tracked": channel_id in tracked,
        "published_at": published,
        "published_age_hours": published_age_hours,
        "duration": duration,
        "privacy": privacy,
        "live_status": live,
        "thumbnail_available": thumbnail,
        "title_prefilter_accepts": plausible_reaction_title(title),
        "search_hit_accepts": subject_reaction_search_hit(evidence),
        "reaction_classifier_accepts": clear_reaction_title(evidence),
        "published_vod": is_published_vod({
            "metadata_confirmed": True,
            "live_broadcast_content": live,
            "live_streaming_details": item.get("liveStreamingDetails"),
        }),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    # Diagnostic only; a valid lookup can expose a failing classification
    # without failing the GitHub job or sending anything to Discord.
    return 0


if __name__ == "__main__":
    sys.exit(main())
