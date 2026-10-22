"""Offline regression tests. No YouTube or Discord network requests."""
import os
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

# Tracker module checks configuration when imported.
os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/test-webhook")

import autoreactions as tracker
import check_live_streams as live
from webhook_delivery import post_webhook


class DeliveryTests(unittest.TestCase):
    def sample(self, n):
        return [{"video_id": f"id-{i}", "title": f"Missioned Souls #{i}",
                 "published_at": f"2026-10-22T{i:02}:00:00Z",
                 "channel": "Demo", "url": "https://youtu.be/demo"} for i in range(1, n + 1)]

    def test_oldest_six_sent_and_bookmark_at_six(self):
        videos = self.sample(10)
        with patch.object(tracker, "send_to_discord", side_effect=lambda v, **kwargs: v) as send, \
             patch.object(tracker, "save_last_run") as save:
            result = tracker.deliver_reactions(videos, 6)
        self.assertEqual([v["video_id"] for v in result], [f"id-{i}" for i in range(1, 7)])
        self.assertEqual([v["video_id"] for v in send.call_args.args[0]], [f"id-{i}" for i in range(1, 7)])
        save.assert_called_once_with(videos[5]["published_at"])

    def test_failure_only_acknowledges_contiguous_prefix(self):
        videos = self.sample(4)
        with patch.object(tracker, "send_to_discord", return_value=videos[:2]), \
             patch.object(tracker, "save_last_run") as save:
            result = tracker.deliver_reactions(videos, 4)
        self.assertEqual(len(result), 2)
        save.assert_called_once_with(videos[1]["published_at"])

    def test_total_failure_does_not_advance_bookmark(self):
        with patch.object(tracker, "send_to_discord", return_value=[]), \
             patch.object(tracker, "save_last_run") as save:
            self.assertEqual(tracker.deliver_reactions(self.sample(2), 6), [])
        save.assert_not_called()

    def test_force_mode_does_not_advance_bookmark(self):
        videos = self.sample(10)
        with patch.object(tracker, "send_to_discord", side_effect=lambda v, **kwargs: v) as send, \
             patch.object(tracker, "save_last_run") as save:
            tracker.deliver_reactions(videos, 6, force_send_all=True)
        self.assertEqual(send.call_args.args[0], videos[-6:])
        save.assert_not_called()

    def test_webhook_retries_429_then_succeeds(self):
        statuses = iter([429, 204])
        sleeps = []
        def fake_post(url, json, timeout):
            status = next(statuses)
            return SimpleNamespace(status_code=status, headers={"Retry-After": "2"})
        self.assertTrue(post_webhook("https://example.invalid", {}, request_post=fake_post, sleeper=sleeps.append))
        self.assertEqual(sleeps, [2.0])

    def test_webhook_rejects_permanent_error_without_retry(self):
        calls = []
        def fake_post(url, json, timeout):
            calls.append(1)
            return SimpleNamespace(status_code=400, headers={})
        self.assertFalse(post_webhook("https://example.invalid", {}, request_post=fake_post, sleeper=lambda _: None))
        self.assertEqual(len(calls), 1)

    def test_live_sender_marks_only_successful_prefix(self):
        streams = self.sample(3)
        with patch.object(live, "post_webhook", side_effect=[True, False]) as post, \
             patch.object(live.time, "sleep"):
            sent = live.send_to_discord(streams)
        self.assertEqual([v["video_id"] for v in sent], ["id-1"])
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
