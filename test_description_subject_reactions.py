"""Offline coverage tests for song-focused titles, including the reported missed ID.

This is a MOCKED metadata scenario for xclrgEcb8mo, not a claim about its
actual YouTube title/uploader/date. Real metadata verification is separate.
No secrets, state writes, API requests or Discord traffic.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/test")

import autoreactions as tracker
import discover_channels as discovery
from new_channel_reaction_evidence import (
    clear_reaction_title, plausible_reaction_title, subject_reaction_search_hit
)

VIDEO_ID = "xclrgEcb8mo"
CHANNEL = "UCTR_PSXLloLzLMBVxnt_kLQ"
DESCRIPTION = "Today we're reacting to Missioned Souls covering Long Time. Great musical performance."


def hit(title, description, vid=VIDEO_ID):
    return {
        "id": {"videoId": vid},
        "snippet": {"title": title, "description": description,
                    "channelId": CHANNEL, "channelTitle": "Test Reactor",
                    "publishedAt": "2026-10-22T18:10:50Z"}
    }


class DescriptionSubjectTests(unittest.TestCase):
    def test_description_supported_reaction_acceptance(self):
        video = {"title": "First Time Hearing Long Time | Reaction",
                 "description": DESCRIPTION}
        self.assertTrue(plausible_reaction_title(video["title"]))
        self.assertTrue(subject_reaction_search_hit(video))
        self.assertTrue(clear_reaction_title(video))
        self.assertTrue(tracker.is_safe_reaction_candidate({
            **video, "video_id": VIDEO_ID, "channel_id": CHANNEL
        }))

    def test_no_false_promotion_of_unrelated_reaction(self):
        self.assertFalse(clear_reaction_title({
            "title": "Boston Long Time Reaction",
            "description": "Today we react to Boston's original 1976 recording."
        }))

    def test_cover_tribute_and_repost_still_excluded(self):
        for title in ("Long Time studio cover", "Long Time reaction archive repost",
                      "Missioned Souls tribute", "Missioned Souls official music video"):
            self.assertFalse(clear_reaction_title({
                "title": title, "description": DESCRIPTION
            }), title)

    def test_search_hit_handoff_carries_exact_video_id_once(self):
        api = MagicMock()
        api.search.return_value.list.return_value.execute.side_effect = [
            {"items": [hit("First Time Hearing Long Time | Reaction", DESCRIPTION)]},
            {"items": [hit("First Time Hearing Long Time | Reaction", DESCRIPTION)]},
        ]
        with patch.object(discovery, "youtube", api), patch.object(
            discovery, "DISCOVERY_PAGES", 2
        ):
            channels, videos = discovery.discover_channels(include_videos=True, targeted=True)
        self.assertEqual(channels, {CHANNEL: "Test Reactor"})
        self.assertEqual([v["video_id"] for v in videos[CHANNEL]], [VIDEO_ID])
        self.assertEqual(api.search.return_value.list.call_count, 2)

    def test_metadata_verification_accepts_description_supported_video(self):
        title = "First Time Hearing Long Time | Reaction"
        playlist = [{
            "title": title, "video_id": VIDEO_ID, "channel_id": CHANNEL,
            "channel": "Test Reactor", "published_at": "2026-10-22T18:10:50Z",
            "url": "https://youtu.be/" + VIDEO_ID, "thumbnail": None,
            "live_broadcast_content": "none", "live_streaming_details": None,
        }]
        youtube = MagicMock()
        youtube.videos.return_value.list.return_value.execute.return_value = {
            "items": [{
                "id": VIDEO_ID, "snippet": {
                    "channelId": CHANNEL, "channelTitle": "Test Reactor",
                    "title": title, "description": DESCRIPTION,
                    "publishedAt": "2026-10-22T18:10:50Z",
                    "liveBroadcastContent": "none",
                    "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/" + VIDEO_ID + "/hqdefault.jpg"}},
                }, "status": {"privacyStatus": "public"},
                "contentDetails": {"duration": "PT9M"},
                "statistics": {},
            }]
        }
        with patch.object(tracker, "youtube", youtube), patch.object(
            tracker, "load_tracked_channels", return_value={
                CHANNEL: {"title": "Test Reactor"}
            }
        ), patch.object(tracker, "fetch_channel_videos", return_value=playlist):
            found, _, scanned = tracker.get_reactions_with_stats()
        self.assertEqual(scanned, {CHANNEL})
        self.assertEqual([v["video_id"] for v in found], [VIDEO_ID])
        self.assertTrue(found[0]["thumbnail"])

if __name__ == "__main__":
    unittest.main()
