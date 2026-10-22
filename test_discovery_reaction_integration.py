"""End-to-end, offline discovery-to-tracker registration safety checks."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/test")
import discover_channels as discovery
import autoreactions as tracker
from reaction_channel_policy import is_official_artist_channel
from channel_exclusions import EXCLUDED_CHANNEL_IDS
from reaction_state import plan_notifications
from tests.test_delivery import blank_state, video

NEW = "UCTR_PSXLloLzLMBVxnt_kLQ"
ARTIST = "UCeTfwcnUVurw7Qrk_KF5Idw"
REG = "2026-10-22T19:01:20Z"
NOW = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)


class DiscoveryIntegrationTests(unittest.TestCase):
    def test_two_page_discovery_registers_reactor_but_not_original_band(self):
        client = MagicMock()
        client.search.return_value.list.return_value.execute.side_effect = [
            {"items": [
                {"snippet": {"channelId": NEW, "channelTitle": "The Mr. J Show",
                             "title": "Missioned Souls reaction"}},
                {"snippet": {"channelId": ARTIST, "channelTitle": "Missioned Souls",
                             "title": "Missioned Souls Original Song"}},
                {"snippet": {"channelId": next(iter(EXCLUDED_CHANNEL_IDS)),
                             "channelTitle": "HCJB",
                             "title": "Missioned Souls reaction"}},
                {"snippet": {"channelId": "unrelated", "channelTitle": "Unrelated",
                             "title": "Other music"}},
            ], "nextPageToken": "second"},
            {"items": [
                {"snippet": {"channelId": NEW, "channelTitle": "The Mr. J Show",
                             "title": "Missioned Souls Studio Cover"}},
                {"snippet": {"channelId": "other-reactor", "channelTitle": "New Reaction Channel",
                             "title": "First time hearing Missioned Souls"}},
            ]},
        ]
        with patch.object(discovery, "youtube", client):
            found = discovery.discover_channels()
        self.assertEqual(found, {
            NEW: "The Mr. J Show",
            "other-reactor": "New Reaction Channel",
        })
        self.assertEqual(client.search.return_value.list.call_count, 2)

        # Feed discovery's newly registered uploader into first scanner state.
        registered = {cid: REG for cid in found}
        item = video(1, channel=NEW, published="2026-10-22T18:10:50Z")
        item["title"] = "Missioned Souls - Total Eclipse of the Heart REACTION"
        state = blank_state()
        plan_notifications(state, [item], {"UC1", *found}, now=NOW,
                           channel_added_at=registered)
        self.assertEqual([v["video_id"] for v in state["pending"]], [item["video_id"]])

    def test_official_artist_uploads_excluded_without_changing_live_checker(self):
        self.assertTrue(is_official_artist_channel(ARTIST))
        self.assertFalse(tracker.is_missioned_souls_reaction(
            "Missioned Souls New Cover", channel_id=ARTIST))
        self.assertFalse(tracker.is_missioned_souls_reaction(
            "#MissionedSouls Paper Dove Family Band Cover", channel_id=NEW))
        self.assertTrue(tracker.is_missioned_souls_reaction(
            "Missioned Souls Reaction", channel_id=NEW))

    def test_official_artist_omitted_when_loading_tracked_registry(self):
        data = {"channels": {ARTIST: {"title": "Missioned Souls"},
                             NEW: {"title": "The Mr. J Show"}}}
        import json
        from io import StringIO
        with patch("builtins.open", return_value=StringIO(json.dumps(data))):
            channels = tracker.load_tracked_channels()
        self.assertEqual(list(channels), [NEW])


if __name__ == "__main__":
    unittest.main()
