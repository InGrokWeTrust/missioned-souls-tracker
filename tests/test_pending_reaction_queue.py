"""Offline regressions for the unsent reaction queue.

Preserves original Missioned Souls title matching. Never reaches real
YouTube, GitHub delivery history or Discord.
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

import autoreactions as reactions


CHANNEL = "UCTR_PSXLloLzLMBVxnt_kLQ"


def video(vid, when, title="Missioned Souls – Alannah Myles BLACK VELVET COVER"):
    return {
        "video_id": vid, "channel_id": CHANNEL, "channel": "The Mr. J Show",
        "title": title, "published_at": when,
        "url": "https://youtu.be/" + vid,
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "live_broadcast_content": "none", "live_streaming_details": None,
        "metadata_confirmed": True, "duration_sec": 780,
        "view_count": 3, "like_count": 2, "comment_count": 0,
    }


def fake_details(v):
    return {
        "id": v["video_id"],
        "snippet": {
            "channelId": CHANNEL, "channelTitle": v["channel"],
            "title": v["title"], "publishedAt": v["published_at"],
            "liveBroadcastContent": "none",
            "thumbnails": {"medium": {"url": v["thumbnail"]}},
        },
        "status": {"privacyStatus": "public"},
        "statistics": {"viewCount": "3"},
        "contentDetails": {"duration": "PT13M"},
    }


class PendingQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bookmark = Path(self.tmp.name) / "last_run.json"
        self.bookmark.write_text(json.dumps({
            "last_published_at": "2026-10-23T00:00:00Z",
        }), encoding="utf-8")
        self.patcher = patch.object(reactions, "LAST_RUN_FILE", str(self.bookmark))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def state(self):
        return json.loads(self.bookmark.read_text(encoding="utf-8"))

    def test_legacy_bookmark_does_not_require_new_queue_schema(self):
        self.assertEqual(reactions.load_pending_videos(), [])
        self.assertEqual(reactions.load_held_videos(), [])
        self.assertEqual(reactions.load_last_run(), "2026-10-23T00:00:00Z")

    def test_capped_batch_preserves_every_unsent_video(self):
        one = video("video-one01", "2026-10-23T01:00:00Z")
        two = video("video-two02", "2026-10-23T02:00:00Z")
        three = video("video-tre03", "2026-10-23T03:00:00Z")
        records = {v["video_id"]: v for v in (one, two, three)}
        saved = set()
        calls = []
        def scan(**kwargs):
            retries = kwargs.get("held") or []
            calls.append([v["video_id"] for v in retries])
            if len(calls) == 1:
                return [one, two, three], datetime.now(timezone.utc)
            return [records[entry["video_id"]] for entry in retries], datetime.now(timezone.utc)
        def store(kind, vids):
            saved.update(vids)

        with patch.object(reactions, "get_reactions_with_stats", side_effect=scan), patch.object(
            reactions, "read_ids", side_effect=lambda kind: set(saved)
        ), patch.object(reactions, "save_ids", side_effect=store), patch.object(
            reactions, "MAX_TO_SEND", 1
        ), patch.object(reactions.requests, "post",
                       return_value=SimpleNamespace(status_code=204)) as post, patch.object(
            reactions.time, "sleep", return_value=None
        ):
            self.assertEqual([v["video_id"] for v in reactions.run()], ["video-one01"])
            self.assertEqual([v["video_id"] for v in self.state()["pending_videos"]],
                             ["video-two02", "video-tre03"])
            self.assertEqual([v["video_id"] for v in reactions.run()], ["video-two02"])
            self.assertEqual([v["video_id"] for v in reactions.run()], ["video-tre03"])
            self.assertEqual(reactions.run(), [])
            self.assertEqual(post.call_count, 3)
        self.assertEqual(saved, set(records))
        self.assertEqual(self.state()["pending_videos"], [])
        self.assertEqual(self.state()["last_published_at"], "2026-10-23T03:00:00Z")
        self.assertEqual(calls[1], ["video-two02", "video-tre03"])
        self.assertEqual(calls[2], ["video-tre03"])

    def test_same_published_timestamp_not_lost_when_batch_capped(self):
        videos = [video("same-time01", "2026-10-23T01:00:00Z"),
                  video("same-time02", "2026-10-23T01:00:00Z")]
        ids = {v["video_id"]: v for v in videos}
        saved = set()
        with patch.object(reactions, "read_ids", side_effect=lambda kind: set(saved)
        ), patch.object(reactions, "save_ids", side_effect=lambda kind, vs: saved.update(vs)
        ), patch.object(reactions, "get_reactions_with_stats",
                         side_effect=lambda **kw: (
                             [ids[v["video_id"]] for v in kw["held"]] if kw["held"] else videos,
                             datetime.now(timezone.utc))
        ), patch.object(reactions, "MAX_TO_SEND", 1), patch.object(
            reactions.requests, "post",
            return_value=SimpleNamespace(status_code=204)) as post, patch.object(
            reactions.time, "sleep", return_value=None
        ):
            self.assertEqual(len(reactions.run()), 1)
            self.assertEqual(len(reactions.run()), 1)
            self.assertEqual(post.call_count, 2)
        self.assertEqual(saved, set(ids))
        self.assertEqual(self.state()["pending_videos"], [])

    def test_failed_second_discord_webhook_keeps_queue(self):
        a = video("failure-001", "2026-10-23T01:00:00Z")
        b = video("failure-002", "2026-10-23T02:00:00Z")
        c = video("failure-003", "2026-10-23T03:00:00Z")
        saved = set()
        with patch.object(reactions, "read_ids", side_effect=lambda kind: set(saved)
        ), patch.object(reactions, "save_ids", side_effect=lambda kind, vs: saved.update(vs)
        ), patch.object(reactions, "get_reactions_with_stats",
                         return_value=([a, b, c], datetime.now(timezone.utc))
        ), patch.object(reactions.requests, "post", side_effect=[
            SimpleNamespace(status_code=204), SimpleNamespace(status_code=429),
        ]), patch.object(reactions.time, "sleep", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                reactions.run()

        self.assertEqual(saved, {"failure-001"})
        self.assertEqual({v["video_id"] for v in self.state()["pending_videos"]},
                         {"failure-001", "failure-002", "failure-003"})
        # An error before completion must never move the global cutoff.
        self.assertEqual(self.state()["last_published_at"], "2026-10-23T00:00:00Z")

        by_id = {v["video_id"]: v for v in (a, b, c)}
        with patch.object(reactions, "read_ids", side_effect=lambda kind: set(saved)
        ), patch.object(reactions, "save_ids", side_effect=lambda kind, vs: saved.update(vs)
        ), patch.object(reactions, "get_reactions_with_stats",
                         side_effect=lambda **kw: (
                             [by_id[v["video_id"]] for v in kw["held"]],
                             datetime.now(timezone.utc))
        ), patch.object(reactions.requests, "post",
                       return_value=SimpleNamespace(status_code=204)) as post, patch.object(
            reactions.time, "sleep", return_value=None
        ):
            self.assertEqual([v["video_id"] for v in reactions.run()],
                             ["failure-002", "failure-003"])
            self.assertEqual(post.call_count, 2)
        self.assertEqual(saved, {"failure-001", "failure-002", "failure-003"})
        self.assertEqual(self.state()["pending_videos"], [])

    def test_pending_outside_newest_five_is_reconfirmed_via_youtube(self):
        missing_from_playlist = video("queueolder1", "2026-10-23T01:00:00Z")
        reactions.save_notification_queue({}, {
            missing_from_playlist["video_id"]: {
                "channel_id": CHANNEL, "video_id": missing_from_playlist["video_id"],
                "published_at": missing_from_playlist["published_at"],
            }
        })
        fake = MagicMock()
        fake.videos.return_value.list.return_value.execute.return_value = {
            "items": [fake_details(missing_from_playlist)]
        }
        with patch.object(reactions, "load_tracked_channels",
                          return_value={CHANNEL: {"title": "The Mr. J Show"}}
        ), patch.object(reactions, "fetch_channel_videos", return_value=[
            {"video_id": f"other-item{i}", "title": "Different musical group"}
            for i in range(5)
        ]), patch.object(reactions, "youtube", fake), patch.object(
            reactions, "read_ids", return_value=set()
        ), patch.object(reactions, "save_ids"), patch.object(
            reactions.requests, "post",
            return_value=SimpleNamespace(status_code=204)) as post, patch.object(
            reactions.time, "sleep", return_value=None
        ):
            result = reactions.run()
        self.assertEqual([v["video_id"] for v in result], ["queueolder1"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.state()["pending_videos"], [])
        call = fake.videos.return_value.list.call_args.kwargs
        self.assertIn("queueolder1", call["id"])

    def test_two_queue_types_coexist_without_losing_held_vods(self):
        waiting = {"channel_id": CHANNEL, "video_id": "waiting0001",
                   "published_at": "2026-10-23T01:00:00Z"}
        ready = {"channel_id": CHANNEL, "video_id": "ready000001",
                 "published_at": "2026-10-23T02:00:00Z"}
        reactions.save_notification_queue(
            {waiting["video_id"]: waiting}, {ready["video_id"]: ready})
        self.assertEqual(reactions.load_held_videos(), [waiting])
        self.assertEqual(reactions.load_pending_videos(), [ready])
        reactions.save_last_run("2026-10-23T02:00:00Z")
        self.assertEqual(reactions.load_held_videos(), [waiting])
        self.assertEqual(reactions.load_pending_videos(), [ready])

    def test_corrupt_pending_schema_refuses_to_post(self):
        self.bookmark.write_text(json.dumps({
            "last_published_at": "2026-10-23T00:00:00Z",
            "pending_videos": [{"video_id": "x", "channel_id": "",
                                "published_at": "2026-10-23T02:00:00Z"}],
        }), encoding="utf-8")
        with patch.object(reactions, "read_ids", return_value=set()
        ), patch.object(reactions, "get_reactions_with_stats") as scan, patch.object(
            reactions.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "Invalid pending-video identity"):
                reactions.run()
        scan.assert_not_called()
        post.assert_not_called()

    def test_queue_limit_refuses_to_drop_unsent_ids(self):
        ready = {str(i): {
            "channel_id": CHANNEL, "video_id": str(i),
            "published_at": "2026-10-23T03:00:00Z",
        } for i in range(reactions.MAX_PENDING_VODS + 1)}
        with self.assertRaisesRegex(RuntimeError, "capacity"):
            reactions.save_notification_queue({}, ready)
        self.assertNotIn("pending_videos", self.state())

    def test_broad_title_match_not_narrowed_by_queue(self):
        for title in ("Missioned Souls – BLACK VELVET COVER",
                      "Missioned Souls music review – NeVre",
                      "Missioned Souls - Jump performance analysis"):
            self.assertTrue(reactions.is_missioned_souls_reaction(title))


if __name__ == "__main__":
    unittest.main()
