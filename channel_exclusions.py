"""Permanent YouTube-channel exclusions shared by production workflows.

Use exact YouTube channel IDs, never titles/handles (which can change).
Exclusions apply to discovery, ordinary reactions, queued/replay sends,
and active live alerts. Historical Discord posts are not removed.
"""

EXCLUDED_CHANNEL_IDS = frozenset({
    "UCtEs-cB4K1XFnXgLrIWW1Zg",  # HCJB Music Reaction: user-requested opt-out
})


def is_excluded_channel(channel_id):
    return channel_id in EXCLUDED_CHANNEL_IDS
