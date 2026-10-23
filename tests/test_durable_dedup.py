"""Offline durability and duplicate-suppression tests; no YouTube or Discord traffic."""
import base64
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from types import SimpleNamespace

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as reaction
import check_live_streams as live
import notification_history as history


def vod(vid, stamp):
    return {
        "video_id": vid, "title": "Missioned Souls - Song REACTION",
        "channel": "Demo Reactor", "channel_id": "UCMock",
        "published_at": stamp, "url": "https://youtu.be/" + vid,
        "thumbnail": "https://i.ytimg.com/vi/" + vid + "/hqdefault.jpg",
        "view_count": 3, "like_count": 1, "comment_count": 0,
        "live_broadcast_content": "none",
    }


def livestream(vid):
    obj = vod(vid, "2026-10-23T02:05:00Z")
    obj["live_broadcast_content"] = "live"
    return obj


class RemoteHistoryTests(unittest.TestCase):
    def test_reads_exact_remote_confirmed_ids(self):
        with patch.object(history, "_read_remote", return_value=(["a", "b"], "sha")):
            self.assertEqual(history.read_ids("reactions"), {"a", "b"})

    def test_appends_instead_of_overwriting_history(self):
        captured = []
        def record(method, path, payload=None):
            captured.append((method, path, payload))
            return {}
        with patch.object(history, "_read_remote",
                          return_value=(["old", "other"], "sha-1")), patch.object(
                              history, "_api_request", side_effect=record):
            ids = history.save_ids("reactions", ["old", "new"])
        self.assertEqual(ids, ["old", "other", "new"])
        method, path, payload = captured[0]
        self.assertEqual((method, path), ("PUT", "contents/history/reactions.json"))
        self.assertEqual(payload["branch"], "notification-history")
        self.assertEqual(payload["sha"], "sha-1")
        saved = json.loads(base64.b64decode(payload["content"]))
        self.assertEqual(saved["sent_video_ids"], ["old", "other", "new"])

    def test_conflict_retries_without_losing_other_writer(self):
        fail = HTTPError("https://example.invalid", 409, "conflict", {}, io.BytesIO())
        calls = []
        def update(*args):
            calls.append(args)
            if len(calls) == 1:
                raise fail
            return {}
        with patch.object(history, "_read_remote", side_effect=[
            (["old"], "sha-a"), (["old", "someone-else"], "sha-b"),
        ]), patch.object(history, "_api_request", side_effect=update):
            ids = history.save_ids("live", ["new"])
        self.assertEqual(ids, ["old", "someone-else", "new"])
        self.assertEqual(len(calls), 2)

    def test_remote_read_failure_not_treated_as_empty_history(self):
        with patch.object(history, "_read_remote",
                          side_effect=RuntimeError("ledger unavailable")):
            with self.assertRaisesRegex(RuntimeError, "ledger unavailable"):
                history.read_ids("reactions")


class ReactionDedupTests(unittest.TestCase):
    def test_old_and_v2_bookmark_timestamp_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "last_run.json"
            with patch.object(reaction, "LAST_RUN_FILE", str(path)):
                path.write_text(json.dumps({"version": 2, "last_published_at": "2026-10-22T12:00:00Z",
                                            "seen_video_ids": ["old"]}), encoding="utf-8")
                self.assertEqual(reaction.load_last_run(), "2026-10-22T12:00:00Z")
                path.write_text("{broken", encoding="utf-8")
                with self.assertRaises(json.JSONDecodeError):
                    reaction.load_last_run()

    def test_reaction_delivery_once_and_timestamp_only_after_success(self):
        saved = {"reactions": {"old-video"}, "live": {"old-live"}}
        cutoff = ["2026-10-23T00:00:00Z"]
        checked = [
            vod("old-video", "2026-10-23T01:00:00Z"),
            vod("old-live", "2026-10-23T01:05:00Z"),
            vod("new-a", "2026-10-23T02:00:00Z"),
            vod("new-b", "2026-10-23T03:00:00Z"),
        ]
        posts = []
        def post(url, **kwargs):
            posts.append(kwargs["json"]["embeds"][0]["url"])
            return SimpleNamespace(status_code=204)
        def save(kind, new):
            saved[kind].update(new)
        with patch.object(reaction, "read_ids",
                          side_effect=lambda kind: set(saved[kind])), patch.object(
                              reaction, "save_ids", side_effect=save), patch.object(
                              reaction, "get_reactions_with_stats",
                              return_value=(checked, datetime.now(timezone.utc))), patch.object(
                              reaction, "load_last_run", side_effect=lambda: cutoff[0]), patch.object(
                              reaction, "save_last_run", side_effect=lambda v: cutoff.__setitem__(0, v)), patch.object(
                              reaction.requests, "post", side_effect=post), patch.object(
                              reaction.time, "sleep", return_value=None):
            first = reaction.run()
            second = reaction.run()
        self.assertEqual([v["video_id"] for v in first], ["new-a", "new-b"])
        self.assertEqual(second, [])
        self.assertEqual(posts, ["https://youtu.be/new-a", "https://youtu.be/new-b"])
        self.assertEqual(cutoff[0], "2026-10-23T03:00:00Z")
        self.assertEqual(saved["reactions"], {"old-video", "new-a", "new-b"})

    def test_failed_webhook_does_not_advance_timestamp_or_save_id(self):
        ids = []
        records = []
        with patch.object(reaction, "read_ids", return_value=set()), patch.object(
            reaction, "save_ids", side_effect=lambda kind, v: ids.extend(v)
        ), patch.object(reaction, "get_reactions_with_stats", return_value=([
            vod("one", "2026-10-23T02:00:00Z"),
            vod("two", "2026-10-23T03:00:00Z")], datetime.now(timezone.utc))
        ), patch.object(reaction, "load_last_run", return_value="2026-10-23T00:00:00Z"), patch.object(
            reaction, "save_last_run", side_effect=lambda stamp: records.append(stamp)
        ), patch.object(reaction.requests, "post", side_effect=[
            SimpleNamespace(status_code=204), SimpleNamespace(status_code=429),
        ]), patch.object(reaction.time, "sleep", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                reaction.run()
        self.assertEqual(ids, ["one"])
        self.assertEqual(records, [])

    def test_cannot_send_without_remote_history(self):
        with patch.object(reaction, "read_ids", side_effect=RuntimeError("no history")), patch.object(
            reaction.requests, "post") as post, patch.object(reaction, "get_reactions_with_stats") as scan:
            with self.assertRaisesRegex(RuntimeError, "no history"):
                reaction.run()
            post.assert_not_called()
            scan.assert_not_called()

    def test_missing_timestamp_seeds_without_sending(self):
        marks = []
        with patch.object(reaction, "read_ids", return_value=set()), patch.object(
            reaction, "load_last_run", return_value=None
        ), patch.object(reaction, "get_reactions_with_stats", return_value=([
            vod("new", "2026-10-23T05:00:00Z")], datetime.now(timezone.utc))
        ), patch.object(reaction, "save_last_run", side_effect=marks.append
        ), patch.object(reaction.requests, "post") as post:
            self.assertEqual(reaction.run(), [])
        self.assertEqual(marks, ["2026-10-23T05:00:00Z"])
        post.assert_not_called()

    def test_failed_durable_write_stops_without_advancing_bookmark(self):
        with patch.object(reaction, "read_ids", return_value=set()), patch.object(
            reaction, "load_last_run", return_value="2026-10-23T00:00:00Z"
        ), patch.object(reaction, "get_reactions_with_stats", return_value=([
            vod("new", "2026-10-23T02:00:00Z")], datetime.now(timezone.utc))
        ), patch.object(reaction.requests, "post",
                         return_value=SimpleNamespace(status_code=204)
        ), patch.object(reaction, "save_ids",
                        side_effect=RuntimeError("ledger write unavailable")
        ), patch.object(reaction, "save_last_run") as bookmark:
            with self.assertRaisesRegex(RuntimeError, "ledger write unavailable"):
                reaction.run()
            bookmark.assert_not_called()

    def test_capped_batch_delivers_oldest_first_without_losing_newer(self):
        ledger = set()
        cutoff = ["2026-10-23T00:00:00Z"]
        videos = [vod("first", "2026-10-23T01:00:00Z"),
                  vod("second", "2026-10-23T02:00:00Z")]
        with patch.object(reaction, "read_ids", side_effect=lambda kind: set(ledger)), patch.object(
            reaction, "save_ids", side_effect=lambda kind, new: ledger.update(new)
        ), patch.object(reaction, "get_reactions_with_stats",
                        return_value=(videos, datetime.now(timezone.utc))
        ), patch.object(reaction, "load_last_run", side_effect=lambda: cutoff[0]
        ), patch.object(reaction, "save_last_run",
                        side_effect=lambda v: cutoff.__setitem__(0, v)
        ), patch.object(reaction, "MAX_TO_SEND", 1), patch.object(
            reaction.requests, "post", return_value=SimpleNamespace(status_code=204)
        ) as post, patch.object(reaction.time, "sleep", return_value=None):
            self.assertEqual([v["video_id"] for v in reaction.run()], ["first"])
            self.assertEqual([v["video_id"] for v in reaction.run()], ["second"])
            self.assertEqual(post.call_count, 2)
            self.assertEqual(cutoff[0], "2026-10-23T02:00:00Z")

    def test_force_send_all_still_respects_confirmed_history(self):
        with patch.object(reaction, "read_ids", side_effect=[{"old"}, set()]), patch.object(
            reaction, "load_last_run", return_value="2026-10-23T05:00:00Z"
        ), patch.object(reaction, "get_reactions_with_stats", return_value=([
            vod("old", "2026-10-23T03:00:00Z")], datetime.now(timezone.utc))
        ), patch.object(reaction, "FORCE_SEND_ALL", True), patch.object(
            reaction.requests, "post") as post:
            self.assertEqual(reaction.run(), [])
            post.assert_not_called()


class LiveDedupTests(unittest.TestCase):
    def test_cross_mode_dedup_and_confirmed_delivery(self):
        saved = {"reactions": {"already-reacted"}, "live": {"previously-live"}}
        ids = []
        videos = [livestream("already-reacted"), livestream("previously-live"),
                  livestream("new-live")]
        def store(kind, new):
            saved[kind].update(new)
        with patch.object(live, "read_ids", side_effect=lambda kind: set(saved[kind])), patch.object(
            live, "save_ids", side_effect=store), patch.object(
            live, "load_state", return_value=([], None)), patch.object(
            live, "save_state", side_effect=lambda v: ids.append(list(v))), patch.object(
            live, "find_active_live_streams", return_value=videos
        ), patch.object(live.requests, "post", return_value=SimpleNamespace(status_code=204)) as post, patch.object(
            live.time, "sleep", return_value=None):
            first = live.run()
            second = live.run()
        self.assertEqual([v["video_id"] for v in first], ["new-live"])
        self.assertEqual(second, [])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(saved["live"], {"previously-live", "new-live"})
        self.assertEqual(ids[0], ["new-live"])

    def test_failed_live_webhook_does_not_record_unsent_id(self):
        with patch.object(live, "read_ids", return_value=set()), patch.object(
            live, "load_state", return_value=([], None)
        ), patch.object(live, "find_active_live_streams", return_value=[livestream("fail")]
        ), patch.object(live.requests, "post", return_value=SimpleNamespace(status_code=500)
        ), patch.object(live, "save_ids") as record, patch.object(live, "save_state") as save:
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                live.run()
            record.assert_not_called()
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
