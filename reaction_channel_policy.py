"""Source-channel safety for ordinary reaction notifications.

The official band upload channel contains original performances, not
third-party reactions. The live-stream checker remains independent.
"""
OFFICIAL_MISSIONED_SOULS_CHANNEL_IDS = frozenset({
    "UCeTfwcnUVurw7Qrk_KF5Idw",  # Official Missioned Souls channel
})


def is_official_artist_channel(channel_id):
    return channel_id in OFFICIAL_MISSIONED_SOULS_CHANNEL_IDS
