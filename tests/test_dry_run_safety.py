"""Offline read-only dry-run tests; no real API calls or Discord messages.

Dry-run must never change the tracker bookmark, live bookmark or confirmed
remote sent-video ledger, even when videos would otherwise qualify.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as reaction
import check_live_streams as live
import notification_history as history

CID = "UCTR_PSXLloLzLMBVxnt_kLQ"
VID = "Etztcp21KUw"


def sample(vid=VID, stamp="2026-10-23T08:00:00Z", broadcast="none"):
    return {
        "video_id": vid,
        "channel_id": CID,
        "channel": "The Mr. J Show",
        "title": "Missioned Souls – BLACK VELVET COVER",
        "published_at": stamp,
        "url": f"https://youtu.be/{vid}",
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "live_broadcast_content": broadcast,
        "live_streaming_details": None,
        "metadata_confirmed": True,
        "duration_sec": 500,
        "view_count": 0,
        "like_count": 0,
        "comment_count": 0,
    }


class DryRunReactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bookmark = Path(self.tmp.name) / "last_run.json"
        self.bookmark.write_text(json.dumps({
            "last_published_at": "2026-10-23T00:00:00Z",
            "held_videos": [],
            "pending_videos": [],
        }, indent=2), encoding="utf-8")
        self.p = patch.object(reaction, "LAST_RUN_FILE", str(self.bookmark))
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_eligible_reaction_preview_never_posts_or_writes(self):
        original = self.bookmark.read_bytes()
        output = io.StringIO()
        with patch.object(reaction, "DRY_RUN", True), patch.object(
            reaction, "read_ids", return_value=set()
        ), patch.object(reaction, "get_reactions_with_stats",
                        return_value=([sample()], None)), patch.object(
            reaction, "save_ids") as write_remote, patch.object(
            reaction.requests, "post") as post, patch.object(
            reaction, "send_to_discord") as sender, redirect_stdout(output):
            result = reaction.run()
        self.assertEqual(result, [])
        self.assertIn("WOULD SEND:", output.getvalue())
        self.assertIn(VID, output.getvalue())
        self.assertEqual(self.bookmark.read_bytes(), original)
        post.assert_not_called()
        sender.assert_not_called()
        write_remote.assert_not_called()

    def test_queued_video_and_held_video_simulate_without_mutation(self):
        old_id = "queueold001"
        wait_id = "waiting0001"
        saved = {
            "last_published_at": "2026-10-23T09:00:00Z",
            "held_videos": [{
                "channel_id": CID, "video_id": wait_id,
                "published_at": "2026-10-23T01:00:00Z",
            }],
            "pending_videos": [{
                "channel_id": CID, "video_id": old_id,
                "published_at": "2026-10-23T02:00:00Z",
            }],
        }
        self.bookmark.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        before = self.bookmark.read_bytes()
        v = sample(old_id, "2026-10-23T02:00:00Z")
        held = sample(wait_id, "2026-10-23T01:00:00Z", broadcast="upcoming")
        output = io.StringIO()
        with patch.object(reaction, "DRY_RUN", True), patch.object(
            reaction, "read_ids", return_value=set()
        ), patch.object(reaction, "get_reactions_with_stats",
                        return_value=([v, held], None)) as scan, patch.object(
            reaction.requests, "post") as post, redirect_stdout(output):
            self.assertEqual(reaction.run(), [])
        self.assertEqual({r["video_id"] for r in scan.call_args.kwargs["held"]},
                         {old_id, wait_id})
        self.assertIn(old_id, output.getvalue())
        self.assertEqual(self.bookmark.read_bytes(), before)
        post.assert_not_called()

    def test_missing_bookmark_is_not_seeded_in_dry_run(self):
        self.bookmark.unlink()
        with patch.object(reaction, "DRY_RUN", True), patch.object(
            reaction, "read_ids", return_value=set()
        ), patch.object(reaction, "get_reactions_with_stats",
                        return_value=([sample()], None)), patch.object(
            reaction.requests, "post") as post:
            self.assertEqual(reaction.run(), [])
        self.assertFalse(self.bookmark.exists())
        post.assert_not_called()

    def test_direct_reaction_sender_cannot_reach_webhook(self):
        with patch.object(reaction, "DRY_RUN", True), patch.object(
            reaction.requests, "post") as post, patch.object(
            reaction, "save_ids") as remote:
            self.assertEqual(reaction.send_to_discord([{"video_id": VID}]), [])
        post.assert_not_called()
        remote.assert_not_called()

    def test_direct_bookmark_write_is_suppressed(self):
        before = self.bookmark.read_bytes()
        with patch.object(reaction, "DRY_RUN", True):
            reaction.save_last_run("2026-10-23T12:00:00Z")
            reaction.save_notification_queue({}, {
                VID: {"channel_id": CID, "video_id": VID,
                      "published_at": "2026-10-23T08:00:00Z"}
            })
            reaction.save_held_videos({
                VID: {"channel_id": CID, "video_id": VID,
                      "published_at": "2026-10-23T08:00:00Z"}
            })
        self.assertEqual(self.bookmark.read_bytes(), before)

    def test_dry_run_respects_confirmed_delivery_history(self):
        logs = io.StringIO()
        with patch.object(reaction, "DRY_RUN", True), patch.object(
            reaction, "read_ids", side_effect=[{VID}, set()]
        ), patch.object(reaction, "get_reactions_with_stats",
                        return_value=([sample()], None)), redirect_stdout(logs), patch.object(
            reaction.requests, "post") as post:
            self.assertEqual(reaction.run(), [])
        self.assertNotIn("WOULD SEND:", logs.getvalue())
        post.assert_not_called()


class DryRunLiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bookmark = Path(self.tmp.name) / "live_last_run.json"
        self.bookmark.write_text(json.dumps({
            "sent_video_ids": ["already-live"],
            "last_check": "2026-10-23T00:00:00Z",
        }, indent=2), encoding="utf-8")
        self.p = patch.object(live, "LIVE_LAST_RUN_FILE", str(self.bookmark))
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_live_preview_no_discord_or_state_changes(self):
        before = self.bookmark.read_bytes()
        logs = io.StringIO()
        with patch.object(live, "DRY_RUN", True), patch.object(
            live, "read_ids", return_value=set()
        ), patch.object(live, "find_active_live_streams",
                        return_value=[sample(broadcast="live")]), patch.object(
            live.requests, "post") as post, patch.object(
            live, "save_ids") as save_remote, patch.object(
            live, "send_to_discord") as send, redirect_stdout(logs):
            self.assertEqual(live.run(), [])
        self.assertIn("WOULD SEND LIVE:", logs.getvalue())
        self.assertIn(VID, logs.getvalue())
        self.assertEqual(self.bookmark.read_bytes(), before)
        post.assert_not_called()
        save_remote.assert_not_called()
        send.assert_not_called()

    def test_no_livestream_results_writes_nothing(self):
        before = self.bookmark.read_bytes()
        with patch.object(live, "DRY_RUN", True), patch.object(
            live, "read_ids", return_value=set()
        ), patch.object(live, "find_active_live_streams",
                        return_value=[]), patch.object(live.requests, "post") as post:
            self.assertEqual(live.run(), [])
        self.assertEqual(self.bookmark.read_bytes(), before)
        post.assert_not_called()

    def test_direct_live_sender_never_posts(self):
        with patch.object(live, "DRY_RUN", True), patch.object(
            live.requests, "post") as post, patch.object(live, "save_ids") as remote:
            self.assertEqual(live.send_to_discord([{"video_id": VID}]), [])
        post.assert_not_called()
        remote.assert_not_called()

    def test_direct_live_state_save_never_writes(self):
        before = self.bookmark.read_bytes()
        with patch.object(live, "DRY_RUN", True):
            live.save_state(["changed"])
        self.assertEqual(self.bookmark.read_bytes(), before)

    def test_known_live_or_reaction_not_previewed(self):
        logs = io.StringIO()
        with patch.object(live, "DRY_RUN", True), patch.object(
            live, "read_ids", side_effect=[{VID}, set()]
        ), patch.object(live, "find_active_live_streams",
                        return_value=[sample(broadcast="live")]), redirect_stdout(logs), patch.object(
            live.requests, "post") as post:
            self.assertEqual(live.run(), [])
        self.assertNotIn("WOULD SEND LIVE:", logs.getvalue())
        post.assert_not_called()


class DryRunHistoryGuardTests(unittest.TestCase):
    def test_remote_confirmed_delivery_history_writes_fail_closed(self):
        with patch.dict(os.environ, {"DRY_RUN": "true"}), patch.object(
            history, "_api_request") as request:
            with self.assertRaisesRegex(RuntimeError, "DRY RUN"):
                history.save_ids("reactions", [VID])
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
