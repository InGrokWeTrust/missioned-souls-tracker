"""Checkpoint 8: reproduce safe reservation recovery after GitHub 409.

Mocked GitHub Contents API only. No real GitHub history writes, YouTube
calls, Discord webhooks or live credentials.
"""
import base64
import copy
import io
import json
import os
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import notification_history as history
import autoreactions as reactions
from test_durable_remote_delivery_gate import reaction_vod

VID = "durableA001"
OTHER = "othervid012"
MARKER = "unconfirmed_discord_video_id"


def error(status):
    return HTTPError("https://example.invalid/github", status,
                     "simulated API error", {}, io.BytesIO())


class StaleReservationHistory:
    """SHA-guarded GitHub Contents, with one intentionally stale branch reader."""
    def __init__(self, stale_reads=0, concurrent=None, pinned_failures=0,
                 pinned_bad=False, deny_status=None):
        self.old = {
            "version": 1, "sent_video_ids": [],
            "reaction_queue": {
                "version": 1, "pending_videos": [], "held_videos": [],
                "last_published_at": "2026-11-11T06:00:00Z",
                "last_complete_scan_at": "2026-11-11T06:00:00+00:00",
            },
        }
        self.now = copy.deepcopy(self.old)
        self.now["reaction_queue"]["last_complete_scan_at"] = "2026-11-11T08:02:43+00:00"
        self.live = {"version": 1, "sent_video_ids": []}
        self.rev = 2
        self.stale_reads = stale_reads
        self.concurrent = concurrent
        self.pinned_failures = pinned_failures
        self.pinned_bad = pinned_bad
        self.deny_status = deny_status
        self.put_attempts = 0
        self.put_successes = 0
        self.pinned_gets = 0
        self.commit = "a" * 40
        self.committed = None
        self.last_write = None

    def response(self, value, sha):
        return {"type": "file", "sha": sha,
                "content": base64.b64encode(json.dumps(value).encode()).decode()}

    def request(self, method, path, payload=None):
        if path.startswith("contents/history/live.json"):
            if method != "GET":
                raise AssertionError("Unexpected live PUT in reservation test")
            return self.response(self.live, "live-1")
        if not path.startswith("contents/history/reactions.json"):
            raise AssertionError("Unexpected API path")
        if method == "GET":
            ref = path.split("?ref=", 1)[1]
            if ref != "notification-history":
                self.pinned_gets += 1
                if ref != self.commit or self.committed is None:
                    raise error(404)
                if self.pinned_failures:
                    self.pinned_failures -= 1
                    raise error(404)
                if self.pinned_bad:
                    return self.response(self.old, "blob-1")
                return self.response(self.committed, f"blob-{self.rev}")
            if self.stale_reads:
                self.stale_reads -= 1
                return self.response(self.old, "blob-1")
            return self.response(self.now, f"blob-{self.rev}")
        if method != "PUT":
            raise AssertionError("Unexpected method")
        self.put_attempts += 1
        if self.deny_status is not None:
            raise error(self.deny_status)
        if self.concurrent is not None:
            if self.concurrent == "sent":
                self.now["sent_video_ids"].append(VID)
            elif self.concurrent == "marker":
                self.now[MARKER] = OTHER
            elif self.concurrent == "live_sent":
                self.live["sent_video_ids"].append(VID)
            self.concurrent = None
            self.rev += 1
            raise error(409)
        if payload["sha"] != f"blob-{self.rev}":
            raise error(409)
        if payload["branch"] != "notification-history":
            raise AssertionError("Attempted wrong history branch")
        self.now = json.loads(base64.b64decode(payload["content"]))
        self.rev += 1
        self.committed = copy.deepcopy(self.now)
        self.last_write = copy.deepcopy(self.now)
        self.put_successes += 1
        return {"content": {"sha": f"blob-{self.rev}"},
                "commit": {"sha": self.commit}}


class Reservation409Tests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(history, "HISTORY_ENABLED", True),
            patch.dict(os.environ, {"DRY_RUN": "false"}),
            patch.object(reactions, "DRY_RUN", False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def reserve(self, fake, video=VID, kind="reactions"):
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep") as sleep:
            result = history.reserve_delivery(kind, video)
            return result, sleep

    def test_stale_branch_sha_after_queue_checkpoint_recovers_409(self):
        fake = StaleReservationHistory(stale_reads=2)
        result, sleep = self.reserve(fake)
        self.assertEqual(result, VID)
        self.assertEqual(fake.put_attempts, 3)
        self.assertEqual(fake.put_successes, 1)
        self.assertEqual(fake.pinned_gets, 1)
        self.assertEqual(fake.now[MARKER], VID)
        self.assertEqual(fake.now["reaction_queue"]["last_complete_scan_at"],
                         "2026-11-11T08:02:43+00:00")
        sleep.assert_called()

    def test_concurrent_confirmed_same_id_must_block_retry(self):
        fake = StaleReservationHistory(concurrent="sent")
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "already durably confirmed"):
                history.reserve_delivery("reactions", VID)
        self.assertEqual(fake.put_successes, 0)
        self.assertNotIn(MARKER, fake.now)

    def test_concurrent_unconfirmed_different_id_must_block(self):
        fake = StaleReservationHistory(concurrent="marker")
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
                history.reserve_delivery("reactions", VID)
        self.assertEqual(fake.put_successes, 0)
        self.assertEqual(fake.now[MARKER], OTHER)

    def test_new_confirmed_live_id_blocks_cross_mode_retry(self):
        fake = StaleReservationHistory(concurrent="live_sent")
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "already durably confirmed"):
                history.reserve_delivery("reactions", VID)
        self.assertEqual(fake.put_successes, 0)

    def test_persistent_conflicts_fail_closed_before_webhook(self):
        fake = StaleReservationHistory(stale_reads=100)
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep"), patch.object(reactions.requests, "post") as webhook:
            with self.assertRaises(HTTPError) as err:
                reactions.send_to_discord(
                    [reaction_vod()], max_to_send=1,
                    run_start=datetime(2026, 11, 11, 8, 4, tzinfo=timezone.utc))
        self.assertEqual(err.exception.code, 409)
        self.assertEqual(fake.put_successes, 0)
        self.assertEqual(fake.put_attempts, history.MAX_HISTORY_WRITE_ATTEMPTS)
        webhook.assert_not_called()

    def test_after_success_stale_branch_read_not_used_for_reservation_verify(self):
        fake = StaleReservationHistory(stale_reads=0)
        result, sleep = self.reserve(fake)
        self.assertEqual(result, VID)
        self.assertEqual(fake.put_successes, 1)
        self.assertEqual(fake.pinned_gets, 1)
        self.assertEqual(fake.now[MARKER], VID)

    def test_pinned_commit_temporarily_unavailable_retries_get_only(self):
        fake = StaleReservationHistory(pinned_failures=1)
        result, sleep = self.reserve(fake)
        self.assertEqual(result, VID)
        self.assertEqual(fake.put_successes, 1)
        self.assertEqual(fake.pinned_gets, 2)
        sleep.assert_called_once()

    def test_pinned_commit_wrong_content_fails_closed(self):
        fake = StaleReservationHistory(pinned_bad=True)
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep"), patch.object(reactions.requests, "post") as webhook:
            with self.assertRaisesRegex(RuntimeError, "not verified"):
                reactions.send_to_discord(
                    [reaction_vod()], max_to_send=1,
                    run_start=datetime(2026, 11, 11, 8, 4, tzinfo=timezone.utc))
        self.assertEqual(fake.put_successes, 1)
        self.assertEqual(fake.now[MARKER], VID)
        webhook.assert_not_called()

    def test_nonconflict_403_never_retried_or_posted(self):
        fake = StaleReservationHistory(deny_status=403)
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep"), patch.object(reactions.requests, "post") as webhook:
            with self.assertRaises(HTTPError) as err:
                reactions.send_to_discord(
                    [reaction_vod()], max_to_send=1,
                    run_start=datetime(2026, 11, 11, 8, 4, tzinfo=timezone.utc))
        self.assertEqual(err.exception.code, 403)
        self.assertEqual(fake.put_attempts, 1)
        webhook.assert_not_called()


if __name__ == "__main__":
    unittest.main()
