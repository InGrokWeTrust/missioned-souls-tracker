"""Fail-closed eligibility for ordinary reaction VOD notifications.

The live checker handles active broadcasts separately. A scheduled premiere or
livestream must finish before the ordinary reaction tracker can notify it.
"""

def is_published_vod(video):
    status = video.get("live_broadcast_content")
    if status not in ("none",):
        return False
    if not video.get("metadata_confirmed", False):
        return False
    details = video.get("live_streaming_details")
    if details is not None:
        if not isinstance(details, dict) or not details.get("actualEndTime"):
            return False
    return True
