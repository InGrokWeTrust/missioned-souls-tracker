"""Offline regressions for bounded, quota-conscious reaction catch-up.

All YouTube, Discord and remote GitHub calls are mocked. No live posts.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as tracker

CHANNEL = "UCMockRecoverChannel"
TARGET = "catchup0001"
SCAN_START = "2026-11-05T00:00:00+00:00"
GLOBAL_CUTOFF = "2026-11-05T02:00:00Z"
RECENT = "2026-11-05T03:00:00Z"
TARGET_STAMP = "2026-11-05T01:00:00Z"
OLD = "2026-11-04T23:00:00Z"


def upload(vid, timestamp, reaction=False):
    title = ("Missioned Souls - song reaction" if reaction else
             "Ordinary unrelated upload")
    return {
        "snippet": {
            "title": title, "publishedAt": timestamp,
            "resourceId": {"videoId": vid},
            "thumbnails": {"medium": {"url":
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}},
            "liveBroadcastContent": "none",
        },
        "contentDetails": {"videoId": vid, "videoPublishedAt": timestamp},
    }


def detail(vid=TARGET):
    return {
        "id": vid,
        "snippet": {
            "channelId": CHANNEL, "channelTitle": "Test Channel",
            "title": "Missioned Souls - song reaction",
            "publishedAt": TARGET_STAMP, "liveBroadcastContent": "none",
            "thumbnails": {"medium": {"url":
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}},
        },
        "status": {"privacyStatus": "public"},
        "statistics": {"viewCount": "3", "likeCount": "1"},
        "contentDetails": {"duration": "PT12M"},
    }


def page50():
    return [upload(f"other{i:06}", RECENT) for i in range(50)]


class CatchUpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bookmark = Path(tmp.name) / "last_run.json"
        self.write_bookmark(complete=True)
        p = patch.object(tracker, "LAST_RUN_FILE", str(self.bookmark))
        p.start()
        self.addCleanup(p.stop)
        q = patch.object(tracker, "DRY_RUN", False)
        q.start()
        self.addCleanup(q.stop)

    def write_bookmark(self, *, complete):
        state = {"last_published_at": GLOBAL_CUTOFF}
        if complete:
            state["last_complete_scan_at"] = SCAN_START
        self.bookmark.write_text(json.dumps(state), encoding="utf-8")

    def fake_api(self, pages):
        api = MagicMock()
        api.playlistItems.return_value.list.return_value.execute.side_effect = pages
        api.videos.return_value.list.return_value.execute.return_value = {
            "items": [detail()]}
        return api

    def test_regular_hour_uses_one_full_page_same_quota_as_five(self):
        api = self.fake_api([{"items": [
            upload(TARGET, TARGET_STAMP, reaction=True),
            upload("older-video", OLD)
        ], "nextPageToken": "unused"}])
        with patch.object(tracker, "youtube", api):
            videos = tracker.fetch_channel_videos(CHANNEL, "Test Channel")
        self.assertEqual([v["video_id"] for v in videos], [TARGET, "older-video"])
        self.assertEqual(api.playlistItems.return_value.list.call_count, 1)
        self.assertEqual(api.playlistItems.return_value.list.call_args.kwargs["maxResults"], 50)
        api.search.assert_not_called()

    def test_second_page_recovers_51st_reaction_older_than_global_cutoff(self):
        api = self.fake_api([
            {"items": page50(), "nextPageToken": "second"},
            {"items": [upload(TARGET, TARGET_STAMP, reaction=True),
                       upload("boundary-old", OLD)]},
        ])
        sent = set()
        with patch.object(tracker, "youtube", api), patch.object(
            tracker, "load_tracked_channels",
            return_value={CHANNEL: {"title": "Test Channel"}}
        ), patch.object(tracker, "read_ids", side_effect=lambda kind: set(sent)
        ), patch.object(tracker, "save_ids", side_effect=lambda kind, ids: sent.update(ids)
        ), patch.object(tracker.requests, "post",
                       return_value=SimpleNamespace(status_code=204)) as post, patch.object(
            tracker.time, "sleep"):
            delivered = tracker.run()
        self.assertEqual([v["video_id"] for v in delivered], [TARGET])
        self.assertEqual(sent, {TARGET})
        self.assertEqual(post.call_count, 1)
        self.assertEqual(api.playlistItems.return_value.list.call_count, 2)
        calls = api.playlistItems.return_value.list.call_args_list
        self.assertNotIn("pageToken", calls[0].kwargs)
        self.assertEqual(calls[1].kwargs["pageToken"], "second")
        self.assertEqual(json.loads(self.bookmark.read_text())["last_published_at"],
                         GLOBAL_CUTOFF)  # Never regress the global notification cutoff.
        self.assertIn("last_complete_scan_at", json.loads(self.bookmark.read_text()))

    def test_more_than_two_pages_refuses_to_advance_or_post(self):
        api = self.fake_api([
            {"items": page50(), "nextPageToken": "second"},
            {"items": page50(), "nextPageToken": "third"},
        ])
        before = self.bookmark.read_bytes()
        with patch.object(tracker, "youtube", api), patch.object(
            tracker, "load_tracked_channels",
            return_value={CHANNEL: {"title": "Test Channel"}}
        ), patch.object(tracker, "read_ids", return_value=set()
        ), patch.object(tracker.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "Catch-up capacity exceeded"):
                tracker.run()
        post.assert_not_called()
        self.assertEqual(self.bookmark.read_bytes(), before)
        self.assertEqual(api.playlistItems.return_value.list.call_count, 2)

    def test_failed_second_page_does_not_advance_or_notify(self):
        api = self.fake_api([
            {"items": page50(), "nextPageToken": "second"},
            RuntimeError("sensitive token do not print"),
        ])
        before = self.bookmark.read_bytes()
        with patch.object(tracker, "youtube", api), patch.object(
            tracker, "load_tracked_channels",
            return_value={CHANNEL: {"title": "Test Channel"}}
        ), patch.object(tracker, "read_ids", return_value=set()
        ), patch.object(tracker.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "Incomplete YouTube channel scan") as raised:
                tracker.run()
        self.assertNotIn("sensitive token", str(raised.exception))
        post.assert_not_called()
        self.assertEqual(self.bookmark.read_bytes(), before)

    def test_legacy_bookmark_gets_fifty_once_then_checkpoint(self):
        self.write_bookmark(complete=False)
        api = self.fake_api([{
            "items": [upload("old-id", OLD)], "nextPageToken": "ignored",
        }])
        with patch.object(tracker, "youtube", api), patch.object(
            tracker, "load_tracked_channels",
            return_value={CHANNEL: {"title": "Test Channel"}}
        ), patch.object(tracker, "read_ids", return_value=set()
        ), patch.object(tracker.requests, "post") as post:
            self.assertEqual(tracker.run(), [])
        post.assert_not_called()
        self.assertEqual(api.playlistItems.return_value.list.call_count, 1)
        self.assertIsNotNone(tracker.load_complete_scan())

    def test_dry_run_preserves_scan_bookmark_and_disables_posts(self):
        before = self.bookmark.read_bytes()
        api = self.fake_api([{"items": [upload("old-id", OLD)]}])
        with patch.object(tracker, "DRY_RUN", True), patch.object(
            tracker, "youtube", api
        ), patch.object(tracker, "load_tracked_channels",
                       return_value={CHANNEL: {"title": "Test Channel"}}
        ), patch.object(tracker, "read_ids", return_value=set()
        ), patch.object(tracker.requests, "post") as post:
            self.assertEqual(tracker.run(), [])
        post.assert_not_called()
        self.assertEqual(self.bookmark.read_bytes(), before)

    def test_missing_registry_never_marks_a_complete_scan(self):
        before = self.bookmark.read_bytes()
        with patch.object(tracker, "read_ids", return_value=set()), patch.object(
            tracker, "load_tracked_channels", return_value={}
        ), patch.object(tracker.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "No tracked channels"):
                tracker.run()
        post.assert_not_called()
        self.assertEqual(self.bookmark.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
