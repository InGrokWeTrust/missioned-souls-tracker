"""Exact YouTube channel exclusions shared by Missioned Souls workflows.

Exclude by immutable YouTube channel ID, not display name or video title.
These exclusions do not change the original broad Missioned Souls title matching.
"""

EXCLUDED_CHANNEL_IDS = frozenset({
    "UCtEs-cB4K1XFnXgLrIWW1Zg",  # HCJB Music Reaction
})


def is_excluded_channel(channel_id):
    return channel_id in EXCLUDED_CHANNEL_IDS
