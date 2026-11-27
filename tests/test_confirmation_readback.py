"""Offline regression for the Nov 27 post-204 stale history read."""
import base64
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import notification_history as history
import autoreactions as reactions
import check_live_streams as live
from report_failure import classify_error, report_failure
from test_durable_remote_delivery_gate import (
    FakeGitHubContents, VID, OTHER, MARKER, error, reaction_vod, live_video,
)

class StaleAfterPost(FakeGitHubContents):
    def __init__(self):
        super().__init__()
        self.files["reactions"]["reaction_queue"] = {
            "version": 1, "pending_videos": [], "held_videos": [],
            "last_published_at": "2026-11-26T13:00:25Z",
            "last_complete_scan_at": "2026-11-26T15:11:01+00:00",
        }
        self.old = copy.deepcopy(self.files)
        self.stale = 0
        self.stale_gets = 0

    def request(self, method, path, payload=None):
        if method == "GET" and path.endswith("?ref=notification-history") and self.stale:
            kind = "reactions" if "reactions.json" in path else "live"
            self.stale -= 1
            self.stale_gets += 1
            return {"type": "file", "sha": "sha-1", "content":
                    base64.b64encode(json.dumps(self.old[kind]).encode()).decode()}
        return super().request(method, path, payload)


class ConfirmationReadbackTests(unittest.TestCase):
    def setUp(self):
        self.fake = StaleAfterPost()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bookmark = Path(tmp.name) / "last_run.json"
        self.live_bookmark = Path(tmp.name) / "live_last_run.json"
        self.live_bookmark.write_text('{"sent_video_ids": []}', encoding="utf-8")
        for p in (
            patch.object(history, "_api_request", side_effect=self.fake.request),
            patch.object(history, "HISTORY_ENABLED", True),
            patch.dict(os.environ, {"DRY_RUN": "false"}),
            patch.object(reactions, "DRY_RUN", False),
            patch.object(live, "DRY_RUN", False),
            patch.object(reactions, "LAST_RUN_FILE", str(self.bookmark)),
            patch.object(live, "LIVE_LAST_RUN_FILE", str(self.live_bookmark)),
            patch.object(history.time, "sleep"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def accepted(self, kind, stale):
        def post(*args, **kwargs):
            self.assertEqual(self.fake.files[kind][MARKER], VID)
            self.fake.stale = stale
            return SimpleNamespace(status_code=204)
        return post

    def test_reaction_post_204_then_two_stale_reads_recovers(self):
        with patch.object(reactions.requests, "post", side_effect=self.accepted("reactions", 2)) as post:
            sent = reactions.send_to_discord(
                [reaction_vod()], max_to_send=1,
                before_post=reactions.mark_discord_attempt,
                after_confirmed=reactions.clear_discord_attempt)
        post.assert_called_once()
        self.assertEqual([v["video_id"] for v in sent], [VID])
        self.assertEqual(self.fake.stale_gets, 2)
        self.assertEqual(len(self.fake.puts), 2)
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [VID])
        self.assertNotIn(MARKER, self.fake.files["reactions"])
        self.assertEqual(self.fake.files["reactions"]["reaction_queue"],
                         self.fake.old["reactions"]["reaction_queue"])
        self.assertNotIn(reactions.UNCERTAIN_DELIVERY_KEY,
                         json.loads(self.bookmark.read_text()))
        with self.assertRaisesRegex(RuntimeError, "already durably confirmed"):
            history.reserve_delivery("live", VID)

    def test_live_post_204_then_stale_read_recovers(self):
        with patch.object(live.requests, "post", side_effect=self.accepted("live", 1)) as post:
            sent = live.send_to_discord([live_video()])
        post.assert_called_once()
        self.assertEqual([v["video_id"] for v in sent], [VID])
        self.assertEqual(self.fake.files["live"]["sent_video_ids"], [VID])
        self.assertNotIn(MARKER, self.fake.files["live"])
        self.assertEqual(len(self.fake.puts), 2)

    def test_persistent_stale_reads_keep_reservation_and_block_restart(self):
        with patch.object(reactions.requests, "post",
                          side_effect=self.accepted("reactions", 100)) as post:
            with self.assertRaisesRegex(RuntimeError, "No matching remote"):
                reactions.send_to_discord(
                    [reaction_vod()], max_to_send=1,
                    before_post=reactions.mark_discord_attempt,
                    after_confirmed=reactions.clear_discord_attempt)
        post.assert_called_once()
        self.assertEqual(self.fake.stale_gets, history.MAX_HISTORY_WRITE_ATTEMPTS)
        self.assertEqual(len(self.fake.puts), 1)
        self.assertEqual(self.fake.files["reactions"][MARKER], VID)
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [])
        self.fake.stale = 0
        with patch.object(reactions, "get_reactions_with_stats") as scan, patch.object(
                reactions.requests, "post") as second:
            with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
                reactions.run()
        scan.assert_not_called()
        second.assert_not_called()

    def test_missing_reservation_does_not_invent_delivery(self):
        with self.assertRaisesRegex(RuntimeError, "No matching remote"):
            history.confirm_delivery("reactions", VID)
        self.assertEqual(self.fake.puts, [])
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [])

    def test_wrong_marker_refuses_mutation_without_retry(self):
        history.reserve_delivery("reactions", OTHER)
        with patch.object(history, "_read_remote_document", wraps=history._read_remote_document) as read:
            with self.assertRaisesRegex(RuntimeError, "Different remote"):
                history.confirm_delivery("reactions", VID)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(self.fake.puts), 1)
        self.assertEqual(self.fake.files["reactions"][MARKER], OTHER)

    def test_permission_denial_does_not_retry(self):
        history.reserve_delivery("reactions", VID)
        self.fake.fail_get = [403]
        with self.assertRaises(Exception) as exc:
            history.confirm_delivery("reactions", VID)
        self.assertEqual(exc.exception.code, 403)
        self.assertEqual(len(self.fake.puts), 1)

    def test_already_committed_confirmation_does_not_write_again(self):
        history.reserve_delivery("reactions", VID)
        history.confirm_delivery("reactions", VID)
        self.assertEqual(history.confirm_delivery("reactions", VID), [VID])
        self.assertEqual(len(self.fake.puts), 2)


class HealthLockTests(unittest.TestCase):
    def test_exact_production_errors_classified(self):
        for message in (
            "Unreconciled durable Discord delivery attempt; review GitHub notification history and Discord before restarting",
            "No matching remote delivery attempt; refusing to infer result",
            "Different remote delivery attempt is active; refusing mutation",
        ):
            with self.subTest(message=message):
                result = classify_error("Traceback (most recent call last):\nRuntimeError: " + message)
                self.assertIsNotNone(result)
                self.assertEqual(result[0], "Discord notification acknowledgement needs review")

    def test_durable_lock_alert_is_static_and_link_free(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracker.log"
            path.write_text("Traceback (most recent call last):\n"
                            "RuntimeError: Unreconciled durable Discord delivery attempt; "
                            "review GitHub notification history and Discord before restarting "
                            "https://example.invalid/secret GIU9aPNaNeg", encoding="utf-8")
            with patch("report_failure.requests.post", return_value=SimpleNamespace(status_code=204)) as post:
                self.assertTrue(report_failure(webhook_url="https://example.invalid/health",
                                               log_path=str(path), workflow="Check Live Streams"))
            post.assert_called_once()
            payload = json.dumps(post.call_args.kwargs["json"])
            self.assertIn("Discord notification acknowledgement needs review", payload)
            self.assertNotIn("https://", payload)
            self.assertNotIn("GIU9aPNaNeg", payload)

if __name__ == "__main__":
    unittest.main()
