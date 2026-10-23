"""Validate and select real YouTube video thumbnails before Discord sends.

Never substitute a blank image or an unrelated video thumbnail. This module
only validates the provided YouTube API URL; Discord rendering still depends
on Discord being able to fetch that image.
"""
from urllib.parse import urlsplit


class MissingVideoThumbnail(RuntimeError):
    """A video alert must remain unsent until its thumbnail is available."""


def valid_thumbnail(url, video_id):
    if not isinstance(url, str) or not isinstance(video_id, str):
        return False
    if not url or not video_id:
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment):
        return False
    if host not in {"i.ytimg.com", "img.youtube.com"}:
        return False
    pieces = parsed.path.split("/")
    return (len(pieces) >= 4 and pieces[1] in ("vi", "vi_webp", "vi_live")
            and pieces[2] == video_id and bool(pieces[3]))


def select_thumbnail(thumbnails, video_id, *, fallback=None):
    """Use existing official YouTube thumbnail metadata; no network call."""
    if isinstance(thumbnails, dict):
        for level in ("medium", "high", "standard", "maxres", "default"):
            data = thumbnails.get(level)
            url = data.get("url") if isinstance(data, dict) else None
            if valid_thumbnail(url, video_id):
                return url
    if valid_thumbnail(fallback, video_id):
        return fallback
    return None


def require_thumbnails(videos):
    """Preflight an entire outgoing batch BEFORE its first Discord webhook."""
    missing = [v.get("video_id", "(unknown)") for v in videos
               if not valid_thumbnail(v.get("thumbnail"), v.get("video_id"))]
    if missing:
        raise MissingVideoThumbnail(
            "YouTube thumbnail unavailable/invalid for video ID(s): "
            + ", ".join(map(str, missing[:10]))
            + "; no Discord text-only messages sent. Retain unsent IDs for retry."
        )
