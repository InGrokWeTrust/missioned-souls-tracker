"""Offline scanner-to-state metadata integration, no real API or webhook."""
import unittest
from unittest.mock import MagicMock, patch

import autoreactions as tracker
from new_channel_reaction_evidence import clear_reaction_title
from tests.test_delivery import video

NEW = "UCTR_PSXLloLzLMBVxnt_kLQ"


class ScannerDescriptionTests(unittest.TestCase):
    def test_youtube_batch_snippet_description_flows_to_backfill_decision(self):
        item = video(1, channel=NEW, published="2026-10-22T18:10:50Z")
        item["title"] = (
            "TOTAL ECLIPSE OF THE HEART by Bonnie Tyler | "
            "Missioned Souls | Family Band Studio Cover"
        )
        item["channel"] = "The Mr. J Show"
        metadata = {
            "items": [{
                "id": item["video_id"],
                "snippet": {
                    "liveBroadcastContent": "none",
                    "description": "#missionedsouls #reaction #reactionvideo #musicianreacts",
                },
                "contentDetails": {"duration": "PT10M"},
                "statistics": {"viewCount": "7", "likeCount": "1"},
            }]
        }
        api = MagicMock()
        api.videos.return_value.list.return_value.execute.return_value = metadata
        with patch.object(tracker, "youtube", api), \
             patch.object(tracker, "load_tracked_channels", return_value={
                 NEW: {"title": "The Mr. J Show"}
             }), \
             patch.object(tracker, "fetch_channel_videos", return_value=[item]):
            scan, _, scanned_ids = tracker.get_reactions_with_stats()
        self.assertEqual(scanned_ids, {NEW})
        self.assertEqual(len(scan), 1)
        self.assertEqual(scan[0]["description"], metadata["items"][0]["snippet"]["description"])
        self.assertTrue(scan[0]["metadata_confirmed"])
        self.assertTrue(clear_reaction_title(scan[0]))
        self.assertEqual(api.videos.return_value.list.call_count, 1)


if __name__ == "__main__":
    unittest.main()
