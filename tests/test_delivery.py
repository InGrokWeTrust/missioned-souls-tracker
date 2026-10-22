"""Offline regression tests: no YouTube or Discord network requests."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/test-webhook")

import autoreactions as tracker
import check_live_streams as live
from reaction_state import StateError, load_state, save_state, plan_notifications, acknowledge
from webhook_delivery import post_webhook

NOW = datetime(2026, 10, 22, 16, tzinfo=timezone.utc)


def video(i, *, channel="UC1", published=None):
    return {
        "video_id": f"id-{i}", "channel_id": channel, "title": f"Missioned Souls #{i}",
        "published_at": published or f"2026-10-22T{i:02}:00:00Z",
        "channel": "Demo", "url": f"https://youtu.be/id-{i}",
    }


def blank_state(channels=("UC1",)):
    return {
        "version": 2, "last_published_at": "2026-10-20T00:00:00Z",
        "seen_video_ids": [], "known_channel_ids": list(channels), "pending": []
    }


class OfflineTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bookmark = str(Path(self.tmp.name) / "last_run.json")
        self.live_bookmark = str(Path(self.tmp.name) / "live_last_run.json")
        self.patches = [
            patch.object(tracker, "LAST_RUN_FILE", self.bookmark),
            patch.object(tracker, "MAX_TO_SEND", 6),
            patch.object(live, "LIVE_LAST_RUN_FILE", self.live_bookmark),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def write_state(self, state=None):
        with open(self.bookmark, "w", encoding="utf-8") as f:
            json.dump(state if state is not None else blank_state(), f)

    @staticmethod
    def all_sent(items, **kwargs):
        callback = kwargs.get("on_success")
        for item in items:
            if callback:
                callback(item)
        return list(items)

    def test_ten_videos_queue_six_then_four_without_duplicates(self):
        self.write_state()
        items = [video(i) for i in range(1, 11)]
        with patch.object(tracker, "send_to_discord", side_effect=self.all_sent) as send:
            first = tracker.run_tracker(items, {"UC1"}, run_start=NOW)
            after_first = load_state(self.bookmark)
            second = tracker.run_tracker(items, {"UC1"}, run_start=NOW)
            third = tracker.run_tracker(items, {"UC1"}, run_start=NOW)
        self.assertEqual(len(first), 6)
        self.assertEqual(len(second), 4)
        self.assertEqual(third, [])
        self.assertEqual(len(after_first["pending"]), 4)
        self.assertEqual(len(load_state(self.bookmark)["seen_video_ids"]), 10)
        self.assertEqual(send.call_count, 2)

    def test_partial_discord_failure_persists_only_successes(self):
        self.write_state()
        with patch.object(tracker, "post_webhook", side_effect=[True, False]), \
             patch.object(tracker.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "Discord delivery failed"):
                tracker.run_tracker([video(i) for i in range(1, 5)], {"UC1"}, run_start=NOW)
        state = load_state(self.bookmark)
        self.assertEqual(state["seen_video_ids"], ["id-1"])
        self.assertEqual([v["video_id"] for v in state["pending"]], ["id-2", "id-3", "id-4"])

    def test_complete_discord_failure_preserves_full_queue(self):
        self.write_state()
        with patch.object(tracker, "post_webhook", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Discord delivery failed"):
                tracker.run_tracker([video(1), video(2)], {"UC1"}, run_start=NOW)
        state = load_state(self.bookmark)
        self.assertEqual(state["seen_video_ids"], [])
        self.assertEqual(len(state["pending"]), 2)

    def test_pending_recoverable_with_empty_next_scan(self):
        self.write_state()
        state = load_state(self.bookmark)
        plan_notifications(state, [video(1)], {"UC1"}, now=NOW)
        save_state(self.bookmark, state)
        with patch.object(tracker, "send_to_discord", side_effect=self.all_sent):
            sent = tracker.run_tracker([], {"UC1"}, run_start=NOW)
        self.assertEqual([x["video_id"] for x in sent], ["id-1"])

    def test_missing_bookmark_fails_closed(self):
        with self.assertRaisesRegex(StateError, "Missing bookmark"):
            tracker.run_tracker([video(1)], {"UC1"}, run_start=NOW)

    def test_corrupt_bookmark_fails_closed(self):
        Path(self.bookmark).write_text("{bad-json", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "Unreadable bookmark"):
            tracker.run_tracker([video(1)], {"UC1"}, run_start=NOW)

    def test_explicit_bootstrap_does_not_post(self):
        with patch.dict(os.environ, {"BOOTSTRAP_STATE_IF_MISSING": "true"}), \
             patch.object(tracker, "send_to_discord") as send:
            self.assertEqual(tracker.run_tracker([video(1)], {"UC1"}, run_start=NOW), [])
        send.assert_not_called()
        self.assertEqual(load_state(self.bookmark)["seen_video_ids"], ["id-1"])

    def test_legacy_timestamp_migrates_without_spam(self):
        self.write_state({"last_published_at": "2026-10-22T05:00:00Z"})
        with patch.object(tracker, "send_to_discord", side_effect=self.all_sent):
            result = tracker.run_tracker(
                [video(4), video(5), video(6)], {"UC1"}, run_start=NOW)
        state = load_state(self.bookmark)
        self.assertEqual([v["video_id"] for v in result], ["id-6"])
        self.assertEqual(state["seen_video_ids"], ["id-4", "id-5", "id-6"])
        self.assertEqual(state["known_channel_ids"], ["UC1"])

    def test_late_discovered_older_published_video_is_sent(self):
        self.write_state({"version": 2, "last_published_at": "2026-10-22T10:00:00Z",
                          "seen_video_ids": ["id-10"], "known_channel_ids": ["UC1"], "pending": []})
        with patch.object(tracker, "send_to_discord", side_effect=self.all_sent):
            delivered = tracker.run_tracker(
                [video(10), video(8)], {"UC1"}, run_start=NOW)
        self.assertEqual([v["video_id"] for v in delivered], ["id-8"])

    def test_equal_published_timestamp_distinct_ids(self):
        self.write_state({"version": 2, "last_published_at": "2026-10-22T10:00:00Z",
                          "seen_video_ids": ["id-10"], "known_channel_ids": ["UC1"], "pending": []})
        other = video(11, published="2026-10-22T10:00:00Z")
        with patch.object(tracker, "send_to_discord", side_effect=self.all_sent):
            delivered = tracker.run_tracker([other], {"UC1"}, run_start=NOW)
        self.assertEqual([v["video_id"] for v in delivered], ["id-11"])

    def test_new_channel_does_not_backfill(self):
        self.write_state(blank_state())
        with patch.object(tracker, "send_to_discord") as send:
            self.assertEqual(tracker.run_tracker([video(7, channel="UC2")], {"UC1", "UC2"}, run_start=NOW), [])
        send.assert_not_called()
        state = load_state(self.bookmark)
        self.assertIn("UC2", state["known_channel_ids"])
        self.assertIn("id-7", state["seen_video_ids"])

    def test_old_discovery_outside_lookback_is_not_backfilled(self):
        self.write_state()
        old = video(1, published="2026-09-01T01:00:00Z")
        with patch.object(tracker, "send_to_discord") as send:
            self.assertEqual(tracker.run_tracker([old], {"UC1"}, run_start=NOW), [])
        send.assert_not_called()

    def test_force_send_does_not_change_state(self):
        self.write_state()
        original = json.loads(Path(self.bookmark).read_text(encoding="utf-8"))
        with patch.object(tracker, "send_to_discord", return_value=[video(1)]):
            tracker.run_tracker([video(1)], {"UC1"}, force_send_all=True, run_start=NOW)
        after = load_state(self.bookmark)
        # Force mode is a replay only. Newly detected IDs remain queued.
        self.assertEqual(after["seen_video_ids"], original["seen_video_ids"])
        self.assertEqual(len(after["pending"]), 1)

    def test_webhook_rate_limit_retried(self):
        statuses = iter([429, 204])
        delays = []
        def fake_post(url, json, timeout):
            return SimpleNamespace(status_code=next(statuses), headers={"Retry-After": "2"})
        self.assertTrue(post_webhook("https://example.invalid", {}, request_post=fake_post, sleeper=delays.append))
        self.assertEqual(delays, [2.0])

    def test_permanent_http_failure_is_not_retried(self):
        calls = []
        def fake_post(url, json, timeout):
            calls.append(1)
            return SimpleNamespace(status_code=400, headers={})
        self.assertFalse(post_webhook("https://example.invalid", {}, request_post=fake_post, sleeper=lambda _: None))
        self.assertEqual(len(calls), 1)

    def test_live_sender_stops_at_failed_post(self):
        with patch.object(live, "post_webhook", side_effect=[True, False]) as post, \
             patch.object(live.time, "sleep"):
            sent = live.send_to_discord([video(1), video(2), video(3)])
        self.assertEqual([v["video_id"] for v in sent], ["id-1"])
        self.assertEqual(post.call_count, 2)

    def test_live_ack_callback_invoked_after_each_confirmed_post(self):
        recorded = []
        with patch.object(live, "post_webhook", side_effect=[True, False]), \
             patch.object(live.time, "sleep"):
            delivered = live.send_to_discord(
                [video(1), video(2)], on_success=lambda stream: recorded.append(stream["video_id"])
            )
        self.assertEqual(recorded, ["id-1"])
        self.assertEqual([v["video_id"] for v in delivered], ["id-1"])

    def test_live_missing_bookmark_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Live bookmark missing"):
            live.load_state()

    def test_live_corrupt_bookmark_fails_closed(self):
        Path(self.live_bookmark).write_text("{bad-json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Live bookmark corrupt"):
            live.load_state()

    def test_live_state_roundtrip(self):
        live.save_state(["one", "two"])
        sent, checked = live.load_state()
        self.assertEqual(sent, ["one", "two"])
        self.assertTrue(checked)


if __name__ == "__main__":
    unittest.main()
