"""Human-confirmed exceptional reactions not identifiable by the band's full name.

These are exact video-ID + actual uploading-channel-ID allowlist entries.
They are not broad title patterns or a channel-wide trust policy. Future
Nitro Neisha uploads must be reviewed before being added. No API or webhook.
"""
from __future__ import annotations

import re

# Frozen human verdict in data/keyword_strong30_reactions_only_adjudication_20261022.json
# (experimental research). User verified independent performance reaction.
VERIFIED_NITRO_VIDEOS = {
    "hf03Voz2QBM": "UC-lB_sG0uviaY9pD07kiwjg",  # Taylor John Hardin Reactions
}
NITRO_TITLE = re.compile(r"\bnitro[\s_-]*n(?:ei|ee)i?sha\b", re.IGNORECASE)


def is_verified_nitro_reaction(video):
    """Require independently confirmed video ID, uploader ID and Nitro title."""
    if not isinstance(video, dict):
        return False
    video_id = video.get("video_id")
    channel_id = video.get("channel_id")
    title = video.get("title")
    return bool(
        video_id in VERIFIED_NITRO_VIDEOS
        and VERIFIED_NITRO_VIDEOS[video_id] == channel_id
        and isinstance(title, str)
        and NITRO_TITLE.search(title)
    )
