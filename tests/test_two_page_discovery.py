"""Offline two-page Discovery regressions; no YouTube credentials or Discord traffic.

Pagination must use the original broad Missioned Souls query on BOTH pages
without restoring strict reaction classifiers.
"""
import copy
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import discover_channels as discovery
from channel_exclusions import EXCLUDED_CHANNEL_IDS

FIRST = "UCAAAa00000000000000001"
SECOND = "UCBBBb00000000000000002"
HCJB = next(iter(EXCLUDED_CHANNEL_IDS))
DATE = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


def hit(channel, vid, title, name="Reaction Channel"):
    return {
        "id": {"videoId": vid},
        "snippet": {
            "channelId": channel,
            "channelTitle": name,
            "title": title,
            "publishedAt": DATE,
        },
    }


def youtube_pages(*pages):
    fake = MagicMock()
    fake.search.return_value.list.return_value.execute.side_effect = list(pages)
    return fake


class TwoPageDiscoveryTests(unittest.TestCase):
    def test_second_page_uses_same_broad_query_and_page_token(self):
        fake = youtube_pages(
            {"items": [hit(FIRST, "AAAAAAAAAAA", "Missioned Souls BLACK VELVET COVER")],
             "nextPageToken": "NEXT_TOKEN"},
            {"items": [hit(SECOND, "BBBBBBBBBBB", "Missioned Souls NeVre music review")],
             "nextPageToken": "THIRD_PAGE_UNUSED"},
        )
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 2):
            found, sources = discovery.discover_channels(include_videos=True)
        self.assertEqual(set(found), {FIRST, SECOND})
        self.assertEqual([x["video_id"] for x in sources[SECOND]], ["BBBBBBBBBBB"])
        self.assertEqual(fake.search.return_value.list.call_count, 2)
        first_call, second_call = fake.search.return_value.list.call_args_list
        for call in (first_call, second_call):
            self.assertEqual(call.kwargs["q"], '"Missioned Souls"')
            self.assertEqual(call.kwargs["type"], "video")
            self.assertEqual(call.kwargs["maxResults"], 50)
            self.assertEqual(call.kwargs["order"], "date")
        self.assertNotIn("pageToken", first_call.kwargs)
        self.assertEqual(second_call.kwargs["pageToken"], "NEXT_TOKEN")

    def test_no_next_page_token_avoids_unneeded_quota_request(self):
        fake = youtube_pages({"items": [hit(FIRST, "AAAAAAAAAAA", "Missioned Souls review")]})
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 2):
            result = discovery.discover_channels()
        self.assertIn(FIRST, result)
        fake.search.return_value.list.assert_called_once()

    def test_optional_single_page_fallback(self):
        fake = youtube_pages({"items": [], "nextPageToken": "MORE_AVAILABLE"})
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 1):
            result = discovery.discover_channels()
        self.assertEqual(result, {})
        fake.search.return_value.list.assert_called_once()

    def test_second_page_channel_not_restricted_by_reaction_keywords(self):
        fake = youtube_pages(
            {"items": [], "nextPageToken": "MORE"},
            {"items": [hit(SECOND, "BBBBBBBBBBB", "A random song or cover without band name")]}
        )
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 2):
            found, source = discovery.discover_channels(include_videos=True)
        self.assertIn(SECOND, found)
        # Original discovery accepts channels broadly, but the precise
        # handoff ID must pass the original Missioned Souls-title condition.
        self.assertNotIn(SECOND, source)

    def test_duplicate_search_video_across_pages_only_handed_off_once(self):
        same = hit(FIRST, "AAAAAAAAAAA", "Missioned Souls BLACK VELVET COVER")
        fake = youtube_pages(
            {"items": [same], "nextPageToken": "SECOND"},
            {"items": [copy.deepcopy(same), hit(FIRST, "CCCCCCCCCCC", "Missioned Souls Music Review")]}
        )
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 2):
            found, sources = discovery.discover_channels(include_videos=True)
        self.assertEqual(len(found), 1)
        self.assertEqual([v["video_id"] for v in sources[FIRST]],
                         ["AAAAAAAAAAA", "CCCCCCCCCCC"])

    def test_second_page_hcjb_is_still_excluded(self):
        fake = youtube_pages(
            {"items": [], "nextPageToken": "NEXT"},
            {"items": [hit(HCJB, "HHHHHHHHHHH", "Missioned Souls review"),
                       hit(SECOND, "BBBBBBBBBBB", "Missioned Souls NeVre review")]}
        )
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 2):
            found, sources = discovery.discover_channels(include_videos=True)
        self.assertNotIn(HCJB, found)
        self.assertNotIn(HCJB, sources)
        self.assertIn(SECOND, found)

    def test_second_page_fails_loudly_without_committing_partial_discovery(self):
        fake = youtube_pages(
            {"items": [hit(FIRST, "AAAAAAAAAAA", "Missioned Souls review")],
             "nextPageToken": "NEXT"},
            RuntimeError("YouTube second-page API unavailable")
        )
        saved = []
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 2), patch.object(
            discovery, "load_tracked_channels", return_value=({}, None)), patch.object(
            discovery, "save_tracked_channels", side_effect=lambda data: saved.append(data)):
            with self.assertRaisesRegex(RuntimeError, "second-page"):
                discovery.run_discovery()
        self.assertEqual(saved, [])

    def test_second_page_handoff_reaches_tracked_channel_records(self):
        fake = youtube_pages(
            {"items": [hit(FIRST, "AAAAAAAAAAA", "Missioned Souls cover")],
             "nextPageToken": "NEXT"},
            {"items": [hit(SECOND, "BBBBBBBBBBB", "Missioned Souls music review")]}
        )
        saved = []
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 2), patch.object(
            discovery, "load_tracked_channels", return_value=({}, None)), patch.object(
            discovery, "save_tracked_channels",
            side_effect=lambda data: saved.append(copy.deepcopy(data))):
            discovery.run_discovery()
        self.assertEqual(set(saved[0]), {FIRST, SECOND})
        self.assertEqual(saved[0][SECOND]["discovery_videos"][0]["video_id"],
                         "BBBBBBBBBBB")

    def test_existing_dormant_channel_reactivation_preserves_registration(self):
        stamp = "2026-01-01T00:00:00Z"
        original = {SECOND: {"title": "Old Name", "added_at": stamp,
                             "last_seen_at": "2025-01-01T00:00:00Z",
                             "dormant": True}}
        fake = youtube_pages(
            {"items": [], "nextPageToken": "NEXT"},
            {"items": [hit(SECOND, "BBBBBBBBBBB", "Missioned Souls song",
                           name="Updated Reactor")]}
        )
        saved = []
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_PAGES", 2), patch.object(
            discovery, "load_tracked_channels", return_value=(original, None)), patch.object(
            discovery, "save_tracked_channels",
            side_effect=lambda data: saved.append(copy.deepcopy(data))):
            discovery.run_discovery()
        self.assertEqual(saved[0][SECOND]["added_at"], stamp)
        self.assertFalse(saved[0][SECOND]["dormant"])
        self.assertEqual(saved[0][SECOND]["title"], "Updated Reactor")

    def test_two_page_config_default_and_upper_bound(self):
        self.assertEqual(discovery.DISCOVERY_PAGES, 2)
        self.assertLessEqual(discovery.DISCOVERY_PAGES, 2)


if __name__ == "__main__":
    unittest.main()
