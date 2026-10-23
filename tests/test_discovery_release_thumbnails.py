"""Regression tests for discovery source-video IDs, thumbnails and VOD release.

These tests use mocked YouTube and Discord calls and NEVER send real notifications.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/unused")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as tracker
import check_live_streams as live
import discover_channels as discovery
from discovery_video_handoff import add_candidates, handoff_for_scan
from thumbnail_policy import MissingVideoThumbnail, valid_thumbnail


CID = "UCTR_PSXLloLzLMBVxnt_kLQ"
VID = "iMmyUPkAoeo"
PIC = f"https://i.ytimg.com/vi/{VID}/hqdefault.jpg"


def now_utc():
    return datetime.now(timezone.utc)


def yt_hit(vid=VID, channel_id=CID, title="Missioned Souls - NeVre reviews",
           publish=None):
    return {
        "id": {"videoId": vid},
        "snippet": {
            "channelId": channel_id, "channelTitle": "The Mr. J Show",
            "title": title, "description": "Music review",
            "publishedAt": publish or (now_utc()-timedelta(minutes=75)).isoformat(),
            "thumbnails": {"medium": {"url": PIC}},
        },
    }


def yt_detail(vid=VID, cid=CID, title="Missioned Souls - NeVre reviews",
              publish=None, broadcast="none", thumbnail=PIC, duration="PT13M",
              status="public", end=None):
    stream = {"actualStartTime": "2026-10-23T06:00:00Z"}
    if end:
        stream["actualEndTime"] = end
    return {
        "id": vid,
        "snippet": {
            "channelId": cid, "channelTitle": "The Mr. J Show",
            "title": title, "publishedAt": publish or (now_utc()-timedelta(minutes=75)).isoformat(),
            "liveBroadcastContent": broadcast,
            "thumbnails": ({"medium": {"url": thumbnail}} if thumbnail else {}),
        },
        "status": {"privacyStatus": status},
        "statistics": {"viewCount": "30", "likeCount": "2", "commentCount": "1"},
        "contentDetails": {"duration": duration},
        "liveStreamingDetails": stream if broadcast in ("live", "upcoming") or end else {},
    }


def reaction(vid=VID, stamp="2026-10-23T07:00:00Z", *,
             broadcast="none", thumbnail=PIC, confirmed=True, handoff=False,
             end=None):
    v = {
        "title": "Missioned Souls - NeVre review",
        "channel_id": CID, "channel": "The Mr. J Show",
        "video_id": vid, "published_at": stamp,
        "url": f"https://youtu.be/{vid}",
        "thumbnail": thumbnail, "live_broadcast_content": broadcast,
        "metadata_confirmed": confirmed, "duration_sec": 780,
        "live_streaming_details": ({"actualStartTime": "2026-10-23T06:00:00Z",
                                    **({"actualEndTime": end} if end else {})}
                                   if broadcast in ("live", "upcoming") or end else None),
        "view_count": 30, "like_count": 2, "comment_count": 1,
    }
    if handoff:
        v["discovery_handoff"] = True
    return v


class DiscoveryHandoffTests(unittest.TestCase):
    def test_discovery_remembers_original_broad_title_hits_without_new_keywords(self):
        fake = MagicMock()
        fake.search.return_value.list.return_value.execute.return_value = {
            "items": [yt_hit(title="Missioned Souls – BLACK VELVET COVER")]
        }
        with patch.object(discovery, "youtube", fake):
            found, source = discovery.discover_channels(include_videos=True)
        self.assertEqual(found, {CID: "The Mr. J Show"})
        self.assertEqual(source[CID][0]["video_id"], VID)
        calls = fake.search.return_value.list.call_args_list
        self.assertEqual(calls[0].kwargs["q"], '"Missioned Souls"')
        self.assertEqual(calls[1].kwargs["q"], '"Missioned Soul"')
        self.assertNotIn("pageToken", calls[0].kwargs)
        self.assertNotIn("pageToken", calls[1].kwargs)
        self.assertEqual(fake.search.return_value.list.call_count, 2)

    def test_description_only_hit_discovers_channel_but_does_not_force_video(self):
        fake = MagicMock()
        fake.search.return_value.list.return_value.execute.return_value = {
            "items": [yt_hit(title="Deep Purple retrospective")]
        }
        with patch.object(discovery, "youtube", fake):
            found, source = discovery.discover_channels(include_videos=True)
        self.assertIn(CID, found)
        self.assertNotIn(CID, source)

    def test_exact_source_id_handoff_works_after_five_newer_uploads(self):
        now = now_utc()
        info = {"title": "The Mr. J Show",
                "added_at": (now-timedelta(minutes=10)).isoformat(),
                "last_seen_at": now.isoformat()}
        saved = add_candidates(info, [{
            "video_id": VID,
            "published_at": (now-timedelta(minutes=75)).isoformat(),
        }], now=now)
        self.assertEqual(saved, 1)
        self.assertEqual(handoff_for_scan({CID: info}, now=now)[0][1], VID)

        fake_youtube = MagicMock()
        fake_youtube.videos.return_value.list.return_value.execute.return_value = {
            "items": [yt_detail()]
        }
        with patch.object(tracker, "load_tracked_channels", return_value={CID: info}), patch.object(
            tracker, "fetch_channel_videos",
            return_value=[{"title": "Unrelated new upload", "video_id": f"irrelevant{i}"}
                          for i in range(5)]
        ), patch.object(tracker, "youtube", fake_youtube):
            rows, started = tracker.get_reactions_with_stats()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["video_id"], VID)
        self.assertTrue(rows[0]["discovery_handoff"])
        self.assertTrue(rows[0]["metadata_confirmed"])
        self.assertEqual(rows[0]["thumbnail"], PIC)

    def test_handoff_rejects_source_mismatched_uploader(self):
        now = now_utc()
        info = {"title": "The Mr. J Show",
                "added_at": (now-timedelta(minutes=10)).isoformat()}
        add_candidates(info, [{"video_id": VID,
                               "published_at": (now-timedelta(minutes=75)).isoformat()}], now=now)
        fake = MagicMock()
        fake.videos.return_value.list.return_value.execute.return_value = {
            "items": [yt_detail(cid="UCAnotherPerson")]}
        with patch.object(tracker, "load_tracked_channels", return_value={CID: info}), patch.object(
            tracker, "fetch_channel_videos", return_value=[]
        ), patch.object(tracker, "youtube", fake):
            rows, _ = tracker.get_reactions_with_stats()
        self.assertEqual(rows, [])

    def test_handoff_respects_original_title_rule_on_reconfirmation(self):
        now = now_utc()
        info = {"title": "The Mr. J Show", "added_at": now.isoformat()}
        add_candidates(info, [{"video_id": VID,
                               "published_at": (now-timedelta(minutes=60)).isoformat()}], now=now)
        fake = MagicMock()
        fake.videos.return_value.list.return_value.execute.return_value = {
            "items": [yt_detail(title="An unrelated song")]}
        with patch.object(tracker, "load_tracked_channels", return_value={CID: info}), patch.object(
            tracker, "fetch_channel_videos", return_value=[]
        ), patch.object(tracker, "youtube", fake):
            rows, _ = tracker.get_reactions_with_stats()
        self.assertEqual(rows, [])

    def test_deferred_id_can_be_rechecked_after_discovery_handoff_expires(self):
        fake = MagicMock()
        fake.videos.return_value.list.return_value.execute.return_value = {
            "items": [yt_detail()]}
        with patch.object(tracker, "load_tracked_channels",
                          return_value={CID: {"title": "The Mr. J Show"}}), patch.object(
            tracker, "fetch_channel_videos", return_value=[]
        ), patch.object(tracker, "youtube", fake):
            rows, _ = tracker.get_reactions_with_stats(held=[{
                "video_id": VID, "channel_id": CID, "published_at": "2026-10-23T05:00:00Z",
            }])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["deferred"])


class ThumbnailAndReleaseTests(unittest.TestCase):
    def test_thumbnail_rejects_wrong_video_or_blank_image(self):
        self.assertTrue(valid_thumbnail(PIC, VID))
        self.assertFalse(valid_thumbnail(PIC, "another-video-id"))
        self.assertFalse(valid_thumbnail(None, VID))
        self.assertFalse(valid_thumbnail("https://unrelated.example/image.png", VID))

    def test_direct_reaction_sender_cannot_send_text_only(self):
        with patch.object(tracker.requests, "post") as post, patch.object(
            tracker, "save_ids") as saved:
            with self.assertRaises(MissingVideoThumbnail):
                tracker.send_to_discord([reaction(thumbnail=None)])
        post.assert_not_called()
        saved.assert_not_called()

    def test_direct_reaction_sender_rejects_upcoming(self):
        with patch.object(tracker.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "Unpublished"):
                tracker.send_to_discord([reaction(broadcast="upcoming")])
        post.assert_not_called()

    def test_scheduled_video_held_then_delivered_once_after_release(self):
        with tempfile.TemporaryDirectory() as folder:
            bookmark = Path(folder) / "last_run.json"
            bookmark.write_text(json.dumps({
                "last_published_at": "2026-10-23T06:00:00Z",
            }), encoding="utf-8")
            uploads = [
                reaction(stamp="2026-10-23T05:00:00Z",
                         broadcast="upcoming", handoff=True),
                reaction(stamp="2026-10-23T05:00:00Z"),
                reaction(stamp="2026-10-23T05:00:00Z"),
            ]
            sent_ids = set()
            def record(kind, ids):
                sent_ids.update(ids)
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), patch.object(
                tracker, "read_ids", side_effect=lambda kind: set(sent_ids)
            ), patch.object(tracker, "save_ids", side_effect=record), patch.object(
                tracker, "get_reactions_with_stats",
                side_effect=lambda **kwargs: ([uploads.pop(0)], now_utc())
            ), patch.object(tracker.requests, "post",
                            return_value=SimpleNamespace(status_code=204)
            ) as post, patch.object(tracker.time, "sleep", return_value=None):
                self.assertEqual(tracker.run(), [])
                waiting = json.loads(bookmark.read_text())["held_videos"]
                self.assertEqual([v["video_id"] for v in waiting], [VID])
                post.assert_not_called()
                delivered = tracker.run()
                self.assertEqual([v["video_id"] for v in delivered], [VID])
                self.assertEqual(tracker.run(), [])
            self.assertEqual(post.call_count, 1)
            self.assertEqual(sent_ids, {VID})
            self.assertEqual(json.loads(bookmark.read_text())["held_videos"], [])

    def test_missing_thumbnail_held_until_image_available_even_past_cutoff(self):
        with tempfile.TemporaryDirectory() as folder:
            bookmark = Path(folder) / "last_run.json"
            bookmark.write_text(json.dumps({
                "last_published_at": "2026-10-23T06:00:00Z",
            }), encoding="utf-8")
            uploads = [
                reaction(stamp="2026-10-23T07:00:00Z", thumbnail=None),
                reaction(stamp="2026-10-23T07:00:00Z"),
            ]
            seen = set()
            def scan(**kwargs):
                if len(uploads) == 1:
                    self.assertEqual(kwargs["held"][0]["video_id"], VID)
                return [uploads.pop(0)], now_utc()
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), patch.object(
                tracker, "read_ids", side_effect=lambda kind: set(seen)
            ), patch.object(tracker, "save_ids", side_effect=lambda kind, ids: seen.update(ids)
            ), patch.object(tracker, "get_reactions_with_stats", side_effect=scan
            ), patch.object(tracker.requests, "post",
                            return_value=SimpleNamespace(status_code=204)
            ) as post, patch.object(tracker.time, "sleep", return_value=None):
                self.assertEqual(tracker.run(), [])
                self.assertEqual(json.loads(bookmark.read_text())["held_videos"][0]["video_id"], VID)
                self.assertEqual([v["video_id"] for v in tracker.run()], [VID])
            self.assertEqual(post.call_count, 1)
            self.assertEqual(json.loads(bookmark.read_text())["held_videos"], [])

    def test_missing_thumbnail_does_not_block_other_valid_video(self):
        other_id = "JjFzKrfhNgg"
        with tempfile.TemporaryDirectory() as folder:
            bookmark = Path(folder) / "last_run.json"
            bookmark.write_text(json.dumps({
                "last_published_at": "2026-10-23T06:00:00Z",
            }), encoding="utf-8")
            first = reaction(vid=VID, stamp="2026-10-23T07:00:00Z", thumbnail=None)
            second = reaction(vid=other_id, stamp="2026-10-23T08:00:00Z",
                              thumbnail=f"https://i.ytimg.com/vi/{other_id}/mqdefault.jpg")
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), patch.object(
                tracker, "read_ids", return_value=set()
            ), patch.object(tracker, "save_ids"), patch.object(
                tracker, "get_reactions_with_stats", return_value=([first, second], now_utc())
            ), patch.object(tracker.requests, "post",
                            return_value=SimpleNamespace(status_code=204)
            ) as post, patch.object(tracker.time, "sleep", return_value=None):
                result = tracker.run()
            self.assertEqual([x["video_id"] for x in result], [other_id])
            self.assertEqual(post.call_count, 1)
            saved = json.loads(bookmark.read_text())
            self.assertEqual(saved["last_published_at"], "2026-10-23T08:00:00Z")
            self.assertEqual(saved["held_videos"][0]["video_id"], VID)

    def test_unconfirmed_video_is_held_not_sent(self):
        with tempfile.TemporaryDirectory() as folder:
            bookmark = Path(folder) / "last_run.json"
            bookmark.write_text(json.dumps({
                "last_published_at": "2026-10-23T06:00:00Z",
            }), encoding="utf-8")
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), patch.object(
                tracker, "read_ids", return_value=set()
            ), patch.object(tracker, "get_reactions_with_stats",
                            return_value=([reaction(confirmed=False)], now_utc())
            ), patch.object(tracker.requests, "post") as post:
                self.assertEqual(tracker.run(), [])
            post.assert_not_called()
            self.assertEqual(json.loads(bookmark.read_text())["held_videos"][0]["video_id"], VID)

    def test_invalid_held_bookmark_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            bookmark = Path(folder) / "last_run.json"
            bookmark.write_text(json.dumps({
                "last_published_at": "2026-10-23T06:00:00Z",
                "held_videos": [{"video_id": VID, "channel_id": "", "published_at": "x"}],
            }), encoding="utf-8")
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), patch.object(
                tracker, "read_ids", return_value=set()
            ), patch.object(tracker.requests, "post") as post:
                with self.assertRaises(ValueError):
                    tracker.run()
            post.assert_not_called()


class LiveReleaseTests(unittest.TestCase):
    def test_live_checker_reconfirms_stream_before_announcement(self):
        fake = MagicMock()
        fake.search.return_value.list.return_value.execute.return_value = {
            "items": [yt_hit()]
        }
        fake.videos.return_value.list.return_value.execute.return_value = {
            "items": [yt_detail(broadcast="upcoming", thumbnail=PIC)]}
        with patch.object(live, "youtube", fake):
            streams = live.find_active_live_streams()
        self.assertEqual(streams, [])

    def test_live_alert_never_posts_without_video_thumbnail(self):
        record = reaction(broadcast="live", thumbnail=None)
        with patch.object(live.requests, "post") as post, patch.object(
            live, "save_ids") as saved:
            with self.assertRaises(MissingVideoThumbnail):
                live.send_to_discord([record])
        post.assert_not_called()
        saved.assert_not_called()

    def test_live_dedup_runner_skips_missing_image_without_marking_delivered(self):
        record = reaction(broadcast="live", thumbnail=None)
        with patch.object(live, "read_ids", return_value=set()), patch.object(
            live, "load_state", return_value=([], None)), patch.object(
            live, "find_active_live_streams", return_value=[record]
        ), patch.object(live, "save_ids") as record_id, patch.object(
            live, "save_state") as save, patch.object(
            live.requests, "post") as post:
            self.assertEqual(live.run(), [])
        record_id.assert_not_called()
        post.assert_not_called()
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
