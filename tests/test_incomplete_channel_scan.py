"""Offline regressions for incomplete YouTube uploads scans.

No real YouTube/GitHub/Discord requests. A channel fetch failure must not be
misreported as an empty channel or advance the global reaction cutoff.
"""
import json
import os
import tempfile
import unittest
from _mock_delivery import patch_sender_delivery
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as tracker

CID_A = "UCFixtureFirstChannel"
CID_B = "UCFixtureSecondChannel"
VID_A = "firstvid001"
VID_B = "secondvid02"
CUTOFF = "2026-11-05T00:00:00Z"
EARLIER = "2026-11-05T01:00:00Z"
LATER = "2026-11-05T02:00:00Z"


def item(channel, video_id, timestamp):
    return {
        "channel_id": channel, "channel": "Example Reactor",
        "video_id": video_id, "title": "Missioned Souls - Studio Cover REACTION",
        "published_at": timestamp, "url": f"https://youtu.be/{video_id}",
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "live_broadcast_content": "none", "live_streaming_details": None,
    }


def details(obj):
    return {
        "id": obj["video_id"],
        "snippet": {
            "channelId": obj["channel_id"], "channelTitle": obj["channel"],
            "title": obj["title"], "publishedAt": obj["published_at"],
            "liveBroadcastContent": "none",
            "thumbnails": {"medium": {"url": obj["thumbnail"]}},
        },
        "status": {"privacyStatus": "public"},
        "statistics": {"viewCount": "2", "likeCount": "1", "commentCount": "0"},
        "contentDetails": {"duration": "PT12M"},
    }


class ChannelScanCompletenessTests(unittest.TestCase):
    def setUp(self):
        patch_sender_delivery(self, tracker)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.bookmark = Path(temp.name) / "last_run.json"
        self.bookmark.write_text(json.dumps({"last_published_at": CUTOFF}),
                                 encoding="utf-8")
        p = patch.object(tracker, "LAST_RUN_FILE", str(self.bookmark))
        p.start()
        self.addCleanup(p.stop)

    def registry(self):
        return {CID_A: {"title": "Reactor A"}, CID_B: {"title": "Reactor B"}}

    def test_playlist_error_is_not_silent_or_leaked_to_logs(self):
        api = MagicMock()
        api.playlistItems.return_value.list.return_value.execute.side_effect = (
            RuntimeError("sensitive-url?key=do-not-print"))
        with patch.object(tracker, "youtube", api):
            with self.assertRaisesRegex(RuntimeError, "Incomplete YouTube channel scan") as raised:
                tracker.fetch_channel_videos(CID_A, "Reactor A")
        self.assertIn(CID_A, str(raised.exception))
        self.assertNotIn("do-not-print", str(raised.exception))

    def test_a_genuinely_empty_uploads_playlist_is_allowed(self):
        api = MagicMock()
        api.playlistItems.return_value.list.return_value.execute.return_value = {"items": []}
        with patch.object(tracker, "youtube", api):
            self.assertEqual(tracker.fetch_channel_videos(CID_A, "Reactor A"), [])

    def test_missing_items_field_is_not_an_empty_playlist(self):
        api = MagicMock()
        api.playlistItems.return_value.list.return_value.execute.return_value = {"kind": "youtube#playlistItemListResponse"}
        with patch.object(tracker, "youtube", api):
            with self.assertRaisesRegex(RuntimeError, "invalid uploads response"):
                tracker.fetch_channel_videos(CID_A, "Reactor A")

    def test_scan_failure_after_first_success_never_reaches_details_or_discord(self):
        prior = self.bookmark.read_bytes()
        first = item(CID_A, VID_A, EARLIER)
        def upload_scan(channel, title):
            if channel == CID_A:
                return [first]
            raise RuntimeError("Incomplete YouTube channel scan for second channel")
        api = MagicMock()
        with patch.object(tracker, "read_ids", return_value=set()), patch.object(
            tracker, "load_tracked_channels", return_value=self.registry()
        ), patch.object(tracker, "fetch_channel_videos", side_effect=upload_scan
        ), patch.object(tracker, "youtube", api), patch.object(
            tracker.requests, "post") as post, patch.object(tracker, "save_ids") as sent:
            with self.assertRaisesRegex(RuntimeError, "Incomplete YouTube channel scan"):
                tracker.run()
        api.videos.assert_not_called()
        post.assert_not_called()
        sent.assert_not_called()
        self.assertEqual(self.bookmark.read_bytes(), prior)

    def test_next_full_scan_recovers_both_reactions_without_duplicate(self):
        earlier = item(CID_B, VID_A, EARLIER)
        later = item(CID_A, VID_B, LATER)
        channel_failed = [True]
        recorded = set()
        def channel_uploads(channel, title):
            if channel == CID_B and channel_failed[0]:
                raise RuntimeError("Incomplete YouTube channel scan for second channel")
            return [later] if channel == CID_A else [earlier]
        api = MagicMock()
        api.videos.return_value.list.return_value.execute.return_value = {
            "items": [details(earlier), details(later)]
        }
        def remote(kind):
            return set(recorded) if kind == "reactions" else set()
        with patch.object(tracker, "read_ids", side_effect=remote), patch.object(
            tracker, "save_ids", side_effect=lambda kind, ids: recorded.update(ids)
        ), patch.object(tracker, "load_tracked_channels", return_value=self.registry()
        ), patch.object(tracker, "fetch_channel_videos", side_effect=channel_uploads
        ), patch.object(tracker, "youtube", api), patch.object(
            tracker.requests, "post", return_value=SimpleNamespace(status_code=204)
        ) as posts, patch.object(tracker.time, "sleep"), patch.object(
            tracker, "MAX_TO_SEND", 10):
            with self.assertRaisesRegex(RuntimeError, "Incomplete YouTube channel scan"):
                tracker.run()
            self.assertEqual(recorded, set())
            self.assertEqual(json.loads(self.bookmark.read_text())["last_published_at"], CUTOFF)
            posts.assert_not_called()

            channel_failed[0] = False
            delivered = tracker.run()
            self.assertEqual([x["video_id"] for x in delivered], [VID_A, VID_B])
            self.assertEqual(recorded, {VID_A, VID_B})
            self.assertEqual(json.loads(self.bookmark.read_text())["last_published_at"], LATER)
            self.assertEqual(json.loads(self.bookmark.read_text()).get("pending_videos"), [])
            self.assertEqual(tracker.run(), [])
            self.assertEqual(posts.call_count, 2)

    def test_existing_held_and_pending_queues_survive_partial_failure(self):
        original = {
            "last_published_at": CUTOFF,
            "held_videos": [{"channel_id": CID_A, "video_id": "heldvideo01",
                             "published_at": EARLIER}],
            "pending_videos": [{"channel_id": CID_B, "video_id": "pending0011",
                                "published_at": LATER}],
        }
        self.bookmark.write_text(json.dumps(original), encoding="utf-8")
        with patch.object(tracker, "read_ids", return_value=set()), patch.object(
            tracker, "load_tracked_channels", return_value=self.registry()
        ), patch.object(tracker, "fetch_channel_videos",
                        side_effect=RuntimeError("Incomplete YouTube channel scan")
        ), patch.object(tracker.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "Incomplete YouTube channel scan"):
                tracker.run()
        self.assertEqual(json.loads(self.bookmark.read_text(encoding="utf-8")), original)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
