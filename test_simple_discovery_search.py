"""End-to-end offline checks for simpler discovery keywords and video-ID handoff."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/unused")
import discover_channels as discovery
from discovery_video_handoff import add_candidates, active_candidates

VIDEO_ID = "7F7-Lma-yu4"
CHANNEL = "UCTR_PSXLloLzLMBVxnt_kLQ"
PUBLISHED = "2026-10-23T02:48:25Z"
NOW = datetime(2026, 10, 23, 4, 0, tzinfo=timezone.utc)


def result(title, description="", video_id=VIDEO_ID):
    return {
        "id": {"videoId": video_id},
        "snippet": {
            "channelId": CHANNEL,
            "channelTitle": "New Music Reaction Channel",
            "title": title,
            "description": description,
            "publishedAt": PUBLISHED,
        },
    }


class SimpleDiscoveryTests(unittest.TestCase):
    def test_broad_pagination_keeps_deduplicated_discovery_handoff(self):
        api = MagicMock()
        api.search.return_value.list.return_value.execute.side_effect = [
            {"items": [result("MISSIONED SOULS - HIGHWAY STAR")],
             "nextPageToken": "page-2"},
            {"items": [result("MISSIONED SOULS - HIGHWAY STAR")]},
        ]
        with patch.object(discovery, "youtube", api), patch.object(
            discovery, "DISCOVERY_PAGES", 2
        ):
            channels, videos = discovery.discover_channels(include_videos=True)
        self.assertEqual(channels, {CHANNEL: "New Music Reaction Channel"})
        self.assertEqual([v["video_id"] for v in videos[CHANNEL]], [VIDEO_ID])
        calls = api.search.return_value.list.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["q"], '"Missioned Souls"')
        self.assertEqual(calls[1].kwargs["q"], '"Missioned Souls"')
        self.assertNotIn("pageToken", calls[0].kwargs)
        self.assertEqual(calls[1].kwargs["pageToken"], "page-2")

    def test_no_second_request_when_first_page_has_no_next_token(self):
        api = MagicMock()
        api.search.return_value.list.return_value.execute.return_value = {
            "items": [result("MISSIONED SOULS - HIGHWAY STAR")]}
        with patch.object(discovery, "youtube", api), patch.object(
            discovery, "DISCOVERY_PAGES", 2
        ):
            discovery.discover_channels()
        self.assertEqual(api.search.return_value.list.call_count, 1)

    def test_song_only_title_with_subject_watching_description_is_handed_off(self):
        api = MagicMock()
        api.search.return_value.list.return_value.execute.side_effect = [
            {"items": [], "nextPageToken": "page-2"},
            {"items": [result(
                "HER SOLO WAS CRAZY !! | HIGHWAY STAR",
                "Today we are checking out this family rock band Missioned Souls playing Highway Star.",
            )]},
        ]
        with patch.object(discovery, "youtube", api), patch.object(
            discovery, "DISCOVERY_PAGES", 2
        ):
            channels, videos = discovery.discover_channels(
                include_videos=True
            )
        self.assertEqual(channels, {CHANNEL: "New Music Reaction Channel"})
        self.assertEqual([x["video_id"] for x in videos[CHANNEL]], [VIDEO_ID])
        info = {"title": "New Music Reaction Channel", "added_at": NOW.isoformat()}
        self.assertEqual(add_candidates(info, videos[CHANNEL], now=NOW), 1)
        self.assertEqual(active_candidates(info, now=NOW)[0]["video_id"], VIDEO_ID)

    def test_no_false_discovery_from_unrelated_subject_link(self):
        api = MagicMock()
        api.search.return_value.list.return_value.execute.return_value = {
            "items": [result(
                "HIGHWAY STAR - cover by somebody else",
                "Subscribe to our channel, and see the Missioned Souls link at the end.",
            )]
        }
        with patch.object(discovery, "youtube", api):
            self.assertEqual(discovery.discover_channels(), {})


if __name__ == "__main__":
    unittest.main()
