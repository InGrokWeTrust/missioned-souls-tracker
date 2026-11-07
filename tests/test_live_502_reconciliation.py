"""Offline tests: Discord live-POST uncertainty and GitHub history 502 recovery.

No real YouTube, GitHub or Discord requests are made in these tests.
"""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import check_live_streams as live

VID = "ABCdef01234"


def video():
    return {
        "video_id": VID,
        "title": "Missioned Souls live rehearsal",
        "channel": "Example Streamer",
        "channel_id": "UCExample",
        "published_at": "2026-11-05T16:00:00Z",
        "url": f"https://youtu.be/{VID}",
        "thumbnail": f"https://i.ytimg.com/vi/{VID}/hqdefault.jpg",
        "live_broadcast_content": "live",
    }


class Live502ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "live_last_run.json"
        self.path.write_text(json.dumps({
            "sent_video_ids": [], "last_check": "2026-11-05T00:00:00Z"
        }), encoding="utf-8")
        p = patch.object(live, "LIVE_LAST_RUN_FILE", str(self.path))
        p.start()
        self.addCleanup(p.stop)
        q = patch.object(live, "DRY_RUN", False)
        q.start()
        self.addCleanup(q.stop)

    def state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_marker_precedes_discord_and_clears_after_durable_ack(self):
        recorded = []
        def fake_post(*args, **kwargs):
            self.assertEqual(self.state()["unconfirmed_discord_video_id"], VID)
            return SimpleNamespace(status_code=204)
        with patch.object(live.requests, "post", side_effect=fake_post) as post, patch.object(
            live, "save_ids", side_effect=lambda kind, ids: recorded.extend(ids)
        ), patch.object(live.time, "sleep"):
            sent = live.send_to_discord([video()])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(recorded, [VID])
        self.assertEqual([v["video_id"] for v in sent], [VID])
        self.assertNotIn("unconfirmed_discord_video_id", self.state())

    def test_502_after_discord_204_blocks_unattended_repost(self):
        failure = HTTPError("https://example.invalid/github-history", 502,
                            "Bad Gateway", {}, io.BytesIO())
        def accepted(*args, **kwargs):
            self.assertEqual(self.state()["unconfirmed_discord_video_id"], VID)
            return SimpleNamespace(status_code=204)
        with patch.object(live, "read_ids", return_value=set()), patch.object(
            live, "find_active_live_streams", return_value=[video()]
        ), patch.object(live, "save_ids", side_effect=failure), patch.object(
            live.requests, "post", side_effect=accepted) as post:
            with self.assertRaises(HTTPError):
                live.run()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.state()["unconfirmed_discord_video_id"], VID)
        with patch.object(live, "read_ids", return_value=set()), patch.object(
            live, "find_active_live_streams"
        ) as scan, patch.object(live.requests, "post") as second_post:
            with self.assertRaisesRegex(RuntimeError, "Unreconciled Discord delivery attempt"):
                live.run()
        scan.assert_not_called()
        second_post.assert_not_called()

    def test_remote_acknowledgement_clears_marker_without_posting(self):
        live.mark_live_discord_attempt(VID)
        with patch.object(live, "read_ids", side_effect=lambda kind: {VID} if kind == "live" else set()
        ), patch.object(live, "find_active_live_streams", return_value=[video()]
        ), patch.object(live.requests, "post") as post:
            self.assertEqual(live.run(), [])
        post.assert_not_called()
        self.assertNotIn("unconfirmed_discord_video_id", self.state())

    def test_timeout_retains_marker_for_review(self):
        with patch.object(live.requests, "post", side_effect=TimeoutError()), patch.object(
            live, "save_ids") as history:
            with self.assertRaisesRegex(RuntimeError, "outcome uncertain"):
                live.send_to_discord([video()])
        history.assert_not_called()
        self.assertEqual(self.state()["unconfirmed_discord_video_id"], VID)

    def test_explicit_http_rejection_clears_marker(self):
        with patch.object(live.requests, "post", return_value=SimpleNamespace(status_code=429)
        ), patch.object(live, "save_ids") as history:
            with self.assertRaisesRegex(RuntimeError, "Discord live HTTP 429"):
                live.send_to_discord([video()])
        history.assert_not_called()
        self.assertNotIn("unconfirmed_discord_video_id", self.state())

    def test_corrupt_state_refuses_youtube_scan_and_post(self):
        self.path.write_text('{"sent_video_ids": [', encoding="utf-8")
        with patch.object(live, "read_ids", return_value=set()), patch.object(
            live, "find_active_live_streams"
        ) as scan, patch.object(live.requests, "post") as post:
            with self.assertRaises(json.JSONDecodeError):
                live.run()
        scan.assert_not_called()
        post.assert_not_called()

    def test_invalid_marker_refuses_youtube_scan_and_post(self):
        data = self.state()
        data["unconfirmed_discord_video_id"] = {"video_id": VID}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(live, "read_ids", return_value=set()), patch.object(
            live, "find_active_live_streams"
        ) as scan, patch.object(live.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "Invalid unconfirmed"):
                live.run()
        scan.assert_not_called()
        post.assert_not_called()

    def test_dry_run_never_modifies_marker_or_posts(self):
        before = self.path.read_bytes()
        with patch.object(live, "DRY_RUN", True), patch.object(
            live, "read_ids", return_value=set()
        ), patch.object(live, "find_active_live_streams", return_value=[video()]
        ), patch.object(live.requests, "post") as post:
            self.assertEqual(live.run(), [])
        post.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
