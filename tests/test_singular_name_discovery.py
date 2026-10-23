"""Singular Missioned Soul discovery and reaction matching, offline only.

No real YouTube/Discord traffic, GitHub writes, historical replays, or strict
reaction-classifier rules.
"""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as reactions
import discover_channels as discovery
from reaction_name_policy import title_mentions_band, discovery_queries
from channel_exclusions import EXCLUDED_CHANNEL_IDS

CID = "UCNewSingularReactor0001"
VID = "PM62MOyitbI"
STAMP = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
TITLE = "Musician Reacts To: Missioned Soul - I I Hate Myself For Loving You"


def hit(cid=CID, vid=VID, title=TITLE):
    return {"id": {"videoId": vid}, "snippet": {
        "channelId": cid, "channelTitle": "Example Musician Reacts",
        "title": title, "publishedAt": STAMP}}


def youtube_responses(*responses):
    fake = MagicMock()
    fake.search.return_value.list.return_value.execute.side_effect = list(responses)
    return fake


class NameMatchingTests(unittest.TestCase):
    def test_singular_celca_title_is_now_accepted(self):
        self.assertTrue(title_mentions_band(TITLE))
        self.assertTrue(reactions.is_missioned_souls_reaction(TITLE))

    def test_plural_remains_broad_and_case_insensitive(self):
        for title in (
            "Missioned Souls - Black Velvet cover",
            "MISSIONED SOULS – JUMP Review",
            "Musician reacts to mIsSiOnEd SoUlS | Crazy Train",
            "This is a Missioned Souls tribute",
            "BLACK VELVET — Missioned Soul reaction",
        ):
            with self.subTest(title=title):
                self.assertTrue(reactions.is_missioned_souls_reaction(title))

    def test_similarly_named_words_not_accepted(self):
        for title in (
            "Missioned Soulmate reaction",
            "Missioned Soulstice band reviews",
            "Unmissioned Soul cover",
            "Missioned Souls2 analysis",
            "Missioned Soulful lyrics",
            "An unrelated rock reaction",
            "",
        ):
            with self.subTest(title=title):
                self.assertFalse(reactions.is_missioned_souls_reaction(title))
        self.assertFalse(reactions.is_missioned_souls_reaction(None))

    def test_custom_channel_name_override_unchanged(self):
        self.assertTrue(title_mentions_band("Another Custom Name", channel_name="Custom Name"))
        self.assertFalse(title_mentions_band(TITLE, channel_name="Custom Name"))
        self.assertEqual(list(discovery_queries("Custom Name", plural_pages=2)),
                         [('"Custom Name"', 2)])

    def test_default_queries_budget_is_two_plus_one(self):
        self.assertEqual(list(discovery_queries("Missioned Souls", plural_pages=2)),
                         [('"Missioned Souls"', 2), ('"Missioned Soul"', 1)])
        self.assertEqual(list(discovery_queries("Missioned Souls", plural_pages=2,
                                                include_singular=False)),
                         [('"Missioned Souls"', 2)])


class SingularDiscoveryTests(unittest.TestCase):
    def test_singular_query_runs_once_even_with_another_page_token(self):
        fake = youtube_responses(
            {"items": [], "nextPageToken": "PLURAL_PAGE_2"},
            {"items": [], "nextPageToken": "IGNORED_PLURAL_PAGE_3"},
            {"items": [hit()], "nextPageToken": "IGNORED_SINGULAR_PAGE_2"},
        )
        with patch.object(discovery, "youtube", fake):
            found, source = discovery.discover_channels(include_videos=True)
        self.assertIn(CID, found)
        self.assertEqual(source[CID][0]["video_id"], VID)
        args = [c.kwargs for c in fake.search.return_value.list.call_args_list]
        self.assertEqual([x["q"] for x in args],
                         ['"Missioned Souls"', '"Missioned Souls"', '"Missioned Soul"'])
        self.assertEqual(args[1]["pageToken"], "PLURAL_PAGE_2")
        self.assertNotIn("pageToken", args[2])
        self.assertEqual(len(args), 3)

    def test_singular_still_runs_when_plural_has_no_second_page(self):
        fake = youtube_responses({"items": []}, {"items": [hit()]})
        with patch.object(discovery, "youtube", fake):
            found, source = discovery.discover_channels(include_videos=True)
        self.assertEqual(found, {CID: "Example Musician Reacts"})
        self.assertEqual(source[CID][0]["video_id"], VID)
        self.assertEqual(fake.search.return_value.list.call_count, 2)

    def test_same_video_from_both_queries_is_not_double_counted(self):
        fake = youtube_responses({"items": [hit()]}, {"items": [copy.deepcopy(hit())]})
        with patch.object(discovery, "youtube", fake):
            found, source = discovery.discover_channels(include_videos=True)
        self.assertEqual(len(found), 1)
        self.assertEqual([v["video_id"] for v in source[CID]], [VID])

    def test_explicit_singular_search_rollback_preserves_plural(self):
        fake = youtube_responses({"items": [hit(title="Missioned Souls review")]})
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "DISCOVERY_INCLUDE_SINGULAR", False):
            found = discovery.discover_channels()
        self.assertIn(CID, found)
        self.assertEqual(fake.search.return_value.list.call_count, 1)
        self.assertEqual(fake.search.return_value.list.call_args.kwargs["q"],
                         '"Missioned Souls"')

    def test_hcjb_excluded_also_from_singular_results(self):
        hcjb = next(iter(EXCLUDED_CHANNEL_IDS))
        fake = youtube_responses(
            {"items": []},
            {"items": [hit(cid=hcjb), hit()]},
        )
        with patch.object(discovery, "youtube", fake):
            found, source = discovery.discover_channels(include_videos=True)
        self.assertEqual(set(found), {CID})
        self.assertEqual(set(source), {CID})

    def test_singular_video_handoff_saved_for_new_channel(self):
        fake = youtube_responses(
            {"items": []},
            {"items": [hit()]},
        )
        saved = []
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "load_tracked_channels", return_value=({}, None)
        ), patch.object(discovery, "save_tracked_channels",
                       side_effect=lambda x: saved.append(copy.deepcopy(x))):
            discovery.run_discovery()
        self.assertEqual(set(saved[0]), {CID})
        self.assertEqual(saved[0][CID]["discovery_videos"][0]["video_id"], VID)

    def test_does_not_handoff_unrelated_video_from_singular_search(self):
        fake = youtube_responses(
            {"items": []}, {"items": [hit(title="Unrelated rock music")]}
        )
        with patch.object(discovery, "youtube", fake):
            found, source = discovery.discover_channels(include_videos=True)
        self.assertIn(CID, found)
        self.assertEqual(source, {})

    def test_partial_singular_query_error_never_commits_registry(self):
        fake = youtube_responses(
            {"items": [hit(title="Missioned Souls review")]},
            RuntimeError("singular search failed"),
        )
        saved = []
        with patch.object(discovery, "youtube", fake), patch.object(
            discovery, "load_tracked_channels", return_value=({}, None)
        ), patch.object(discovery, "save_tracked_channels",
                       side_effect=lambda x: saved.append(copy.deepcopy(x))):
            with self.assertRaisesRegex(RuntimeError, "singular search failed"):
                discovery.run_discovery()
        self.assertEqual(saved, [])


class SingularTrackerTests(unittest.TestCase):
    def test_scan_recognizes_singular_from_existing_channel(self):
        fake = MagicMock()
        fake.videos.return_value.list.return_value.execute.return_value = {
            "items": [{
                "id": VID,
                "snippet": {
                    "title": TITLE, "channelId": CID,
                    "channelTitle": "Existing Reactor",
                    "publishedAt": STAMP,
                    "liveBroadcastContent": "none",
                    "thumbnails": {"medium": {
                        "url": f"https://i.ytimg.com/vi/{VID}/mqdefault.jpg"}},
                },
                "status": {"privacyStatus": "public"},
                "contentDetails": {"duration": "PT12M"},
                "statistics": {"viewCount": "50"},
            }]
        }
        upload = {
            "channel_id": CID, "channel": "Existing Reactor",
            "video_id": VID, "title": TITLE, "published_at": STAMP,
            "url": f"https://youtu.be/{VID}",
            "thumbnail": f"https://i.ytimg.com/vi/{VID}/mqdefault.jpg",
            "live_broadcast_content": "none", "live_streaming_details": None,
        }
        with patch.object(reactions, "load_tracked_channels",
                          return_value={CID: {"title": "Existing Reactor"}}), patch.object(
            reactions, "fetch_channel_videos", return_value=[upload]
        ), patch.object(reactions, "youtube", fake):
            records, _ = reactions.get_reactions_with_stats()
        self.assertEqual([v["video_id"] for v in records], [VID])
        self.assertTrue(records[0]["metadata_confirmed"])

    def test_old_singular_reaction_not_replayed_to_discord(self):
        with tempfile.TemporaryDirectory() as root:
            bookmark = Path(root) / "last_run.json"
            bookmark.write_text(json.dumps({
                "last_published_at": "2026-10-24T00:00:00Z"
            }), encoding="utf-8")
            old_video = {
                "title": TITLE, "channel_id": CID, "video_id": VID,
                "published_at": "2026-10-23T00:00:00Z",
                "thumbnail": f"https://i.ytimg.com/vi/{VID}/mqdefault.jpg",
                "metadata_confirmed": True,
                "live_broadcast_content": "none", "duration_sec": 600,
            }
            with patch.object(reactions, "LAST_RUN_FILE", str(bookmark)), patch.object(
                reactions, "read_ids", return_value=set()
            ), patch.object(reactions, "get_reactions_with_stats",
                           return_value=([old_video], datetime.now(timezone.utc))
            ), patch.object(reactions.requests, "post") as post:
                result = reactions.run()
            self.assertEqual(result, [])
            post.assert_not_called()
            self.assertNotIn("pending_videos", json.loads(bookmark.read_text()))


if __name__ == "__main__":
    unittest.main()
