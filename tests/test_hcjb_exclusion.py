"""Offline tests for the exact HCJB exclusion without changing broad matching."""

import contextlib
import io
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/unused")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as reactions
import check_live_streams as live
import discover_channels as discovery
from channel_exclusions import EXCLUDED_CHANNEL_IDS, is_excluded_channel


HCJB = "UCtEs-cB4K1XFnXgLrIWW1Zg"
GOOD = "UCExampleNormalReactor"


def search_item(channel_id, channel_title="HCJB Music Reaction", title="Missioned Souls - Black Velvet reviews"):
    return {
        "id": {"videoId": "test-video"},
        "snippet": {
            "channelId": channel_id,
            "channelTitle": channel_title,
            "title": title,
            "publishedAt": "2026-10-23T08:00:00Z",
            "liveBroadcastContent": "live",
            "thumbnails": {"medium": {"url": "https://example.invalid/test.jpg"}},
        }
    }


class HCJBExclusionTests(unittest.TestCase):
    def test_exact_id_only_not_broad_title_keyword_ban(self):
        self.assertEqual(EXCLUDED_CHANNEL_IDS, frozenset({HCJB}))
        self.assertTrue(is_excluded_channel(HCJB))
        self.assertFalse(is_excluded_channel(GOOD))
        self.assertFalse(is_excluded_channel("HCJB Music Reaction"))
        self.assertTrue(reactions.is_missioned_souls_reaction(
            "Black Velvet - Alannah Myles, Missioned Souls - NeVre reviews"))
        self.assertTrue(reactions.is_missioned_souls_reaction(
            "MISSIONED SOULS Alannah Myles | BLACK VELVET COVER"))

    def test_old_tracked_registry_hcjb_filtered_at_load(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "tracked.json"
            registry.write_text(json.dumps({"channels": {
                HCJB: {"title": "HCJB Music Reaction"},
                GOOD: {"title": "My Reactor"},
            }}), encoding="utf-8")
            with patch.object(reactions, "TRACKED_CHANNELS_FILE", str(registry)):
                found = reactions.load_tracked_channels()
            self.assertEqual(set(found), {GOOD})

    def test_discovery_drops_hcjb_but_keeps_other_video_hits(self):
        fake = MagicMock()
        fake.search.return_value.list.return_value.execute.return_value = {
            "items": [search_item(HCJB), search_item(GOOD, "NeVre Music Reviews")]
        }
        with patch.object(discovery, "youtube", fake):
            found = discovery.discover_channels()
        self.assertEqual(found, {GOOD: "NeVre Music Reviews"})
        self.assertEqual(fake.search.return_value.list.call_args.kwargs["q"],
                         '"Missioned Souls"')

    def test_discovery_prunes_hcjb_from_existing_registry(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "tracked.json"
            registry.write_text(json.dumps({"channels": {
                HCJB: {"title": "HCJB Music Reaction", "last_seen_at": "2026-10-23T08:00:00Z"},
                GOOD: {"title": "My Reactor", "last_seen_at": "2026-10-23T08:00:00Z"},
            }}), encoding="utf-8")
            mock_youtube = MagicMock()
            mock_youtube.search.return_value.list.return_value.execute.return_value = {"items": []}
            with patch.dict(os.environ, {"TRACKED_CHANNELS_FILE": str(registry)}), patch(
                "googleapiclient.discovery.build", return_value=mock_youtube
            ), contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(discovery.__file__, run_name="__main__")
            saved = json.loads(registry.read_text(encoding="utf-8"))["channels"]
            self.assertEqual(set(saved), {GOOD})

    def test_reaction_final_delivery_guard_blocks_hcjb(self):
        # The final safety check must block even if a test/direct caller
        # bypasses the registry and calls the sender with an HCJB video.
        with patch.object(reactions.requests, "post") as post, patch.object(
            reactions, "save_ids") as history:
            result = reactions.send_to_discord([{"channel_id": HCJB, "video_id": "x"}])
        self.assertEqual(result, [])
        post.assert_not_called()
        history.assert_not_called()

    def test_reaction_run_defends_against_bypassed_channel_scan(self):
        video = {
            "channel_id": HCJB, "video_id": "hcbj-test",
            "published_at": "2026-10-23T08:00:00Z", "title": "Missioned Souls reviews",
        }
        with patch.object(reactions, "read_ids", return_value=set()), patch.object(
            reactions, "load_last_run", return_value="2026-10-23T07:00:00Z"
        ), patch.object(reactions, "get_reactions_with_stats", return_value=( [video], None )
        ), patch.object(reactions.requests, "post") as post:
            result = reactions.run()
        self.assertEqual(result, [])
        post.assert_not_called()

    def test_live_search_ignores_hcjb_by_uploader_id(self):
        fake = MagicMock()
        fake.search.return_value.list.return_value.execute.return_value = {
            "items": [search_item(HCJB),
                      search_item(GOOD, "Other Reactor", "Missioned Souls live reaction")]
        }
        # Search results must now be reconfirmed against live metadata.
        fake.videos.return_value.list.return_value.execute.return_value = {
            "items": [{
                "id": "test-video",
                "snippet": {
                    "channelId": GOOD,
                    "channelTitle": "Other Reactor",
                    "title": "Missioned Souls live reaction",
                    "liveBroadcastContent": "live",
                    "thumbnails": {"medium": {
                        "url": "https://i.ytimg.com/vi/test-video/hqdefault.jpg"}},
                },
                "liveStreamingDetails": {
                    "actualStartTime": "2026-10-23T08:00:00Z",
                },
            }]
        }
        with patch.object(live, "youtube", fake):
            found = live.find_active_live_streams()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["channel_id"], GOOD)

    def test_live_final_delivery_guard_blocks_hcjb(self):
        with patch.object(live.requests, "post") as post, patch.object(
            live, "save_ids") as history:
            result = live.send_to_discord([{"channel_id": HCJB, "video_id": "x"}])
        self.assertEqual(result, [])
        post.assert_not_called()
        history.assert_not_called()

    def test_live_run_defends_against_bypassed_search(self):
        stream = {
            "channel_id": HCJB, "video_id": "hcjb-live-test",
            "published_at": "2026-10-23T08:00:00Z", "title": "Missioned Souls live",
        }
        with patch.object(live, "read_ids", return_value=set()), patch.object(
            live, "load_state", return_value=([], None)
        ), patch.object(live, "find_active_live_streams", return_value=[stream]
        ), patch.object(live.requests, "post") as post, patch.object(
            live, "save_state") as save:
            result = live.run()
        self.assertEqual(result, [])
        post.assert_not_called()
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
