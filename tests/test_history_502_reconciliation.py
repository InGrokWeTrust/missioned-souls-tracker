"""Offline 502-after-Discord regression; never access real YouTube or Discord.

GitHub writes may commit even after a failed HTTP response. An earlier
Discord 204 must not lead to a second webhook POST on the next run.
"""
import io
import json
import os
import tempfile
import unittest
from _mock_delivery import patch_sender_delivery
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as reaction
import notification_history as history
from report_failure import classify_error

VID = "pwilr3k_zmU"
STAMP = "2026-11-05T16:00:00Z"


def transient(status=502):
    return HTTPError("https://example.invalid/github-history", status,
                     "transient", {}, io.BytesIO())


def candidate(vid=VID, stamp=STAMP):
    return {
        "video_id": vid,
        "title": "Missioned Souls — I Hate Myself for Loving You reaction",
        "channel": "Demo Reactor",
        "channel_id": "UCFakeChannelForTests",
        "published_at": stamp,
        "url": f"https://youtu.be/{vid}",
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "view_count": 12, "like_count": 2, "comment_count": 1,
        "live_broadcast_content": "none",
        "metadata_confirmed": True, "duration_sec": 600,
        "live_streaming_details": None,
    }


class TransientHistoryWriteTests(unittest.TestCase):
    def test_502_after_committed_write_reconciles_without_second_put(self):
        sent = []
        def put(method, path, payload=None):
            sent.append((method, path))
            raise transient()
        with patch.object(history, "_read_remote_document", side_effect=[
            ({"version": 1, "sent_video_ids": ["old"]}, "sha-a"), ({"version": 1, "sent_video_ids": ["old", VID]}, "sha-b"),
        ]) as reads, patch.object(history, "_api_request", side_effect=put), patch.object(
            history.time, "sleep"
        ) as sleep:
            result = history.save_ids("reactions", [VID])
        self.assertEqual(result, ["old", VID])
        self.assertEqual(reads.call_count, 2)
        self.assertEqual(len(sent), 1)
        sleep.assert_called_once_with(1)

    def test_502_without_commit_retries_put_with_fresh_remote_sha(self):
        sent = []
        def put(method, path, payload=None):
            sent.append(payload)
            if len(sent) == 1:
                raise transient()
            return {}
        with patch.object(history, "_read_remote_document", side_effect=[
            ({"version": 1, "sent_video_ids": ["old"]}, "sha-a"), ({"version": 1, "sent_video_ids": ["old", "other"]}, "sha-b"),
        ]), patch.object(history, "_api_request", side_effect=put), patch.object(
            history.time, "sleep"
        ):
            result = history.save_ids("reactions", [VID])
        self.assertEqual(result, ["old", "other", VID])
        self.assertEqual([p["sha"] for p in sent], ["sha-a", "sha-b"])

    def test_five_502_failures_do_not_falsely_acknowledge(self):
        with patch.object(history, "_read_remote_document",
                          return_value=({"version": 1, "sent_video_ids": []}, "same-sha")) as reads, patch.object(
            history, "_api_request", side_effect=transient()
        ) as puts, patch.object(history.time, "sleep") as sleep:
            with self.assertRaises(HTTPError) as failure:
                history.save_ids("reactions", [VID])
        self.assertEqual(failure.exception.code, 502)
        self.assertEqual(puts.call_count, 5)
        self.assertEqual(reads.call_count, 5)
        self.assertEqual(sleep.call_count, 4)

    def test_transient_remote_read_502_also_retries(self):
        with patch.object(history, "_read_remote_document", side_effect=[
            transient(), ({"version": 1, "sent_video_ids": ["old"]}, "sha-a"),
        ]), patch.object(history, "_api_request", return_value={}) as put, patch.object(
            history.time, "sleep"
        ) as sleep:
            self.assertEqual(history.save_ids("reactions", [VID]), ["old", VID])
        put.assert_called_once()
        sleep.assert_called_once()

    def test_403_is_not_retried_or_reinterpreted_as_success(self):
        with patch.object(history, "_read_remote_document", return_value=({"version": 1, "sent_video_ids": []}, "sha")), patch.object(
            history, "_api_request", side_effect=transient(403)
        ) as put, patch.object(history.time, "sleep") as sleep:
            with self.assertRaises(HTTPError):
                history.save_ids("reactions", [VID])
        put.assert_called_once()
        sleep.assert_not_called()


class DiscordAttemptBookmarkTests(unittest.TestCase):
    def setUp(self):
        patch_sender_delivery(self, reaction)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "last_run.json"
        self.path.write_text(json.dumps({
            "last_published_at": "2026-11-05T14:00:00Z",
            "pending_videos": [],
            "held_videos": [],
        }), encoding="utf-8")
        patcher = patch.object(reaction, "LAST_RUN_FILE", str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _run(self, *, remote_ids, history_write, post):
        with patch.object(reaction, "read_ids",
                          side_effect=lambda kind: set(remote_ids) if kind == "reactions"
                          else set()), patch.object(
            reaction, "get_reactions_with_stats",
            return_value=([candidate()], datetime.now(timezone.utc))
        ), patch.object(reaction, "save_ids", side_effect=history_write), patch.object(
            reaction.requests, "post", side_effect=post
        ) as sender, patch.object(reaction.time, "sleep"):
            return sender, reaction.run()

    def test_marker_written_before_discord_and_cleared_after_ledger_ack(self):
        delivered = set()
        def post(url, **kwargs):
            self.assertEqual(self.state()["unconfirmed_discord_video_id"], VID)
            return SimpleNamespace(status_code=204)
        def save(kind, ids):
            delivered.update(ids)
        sender, result = self._run(remote_ids=delivered, history_write=save, post=post)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual([v["video_id"] for v in result], [VID])
        self.assertNotIn("unconfirmed_discord_video_id", self.state())
        self.assertIn(VID, delivered)

    def test_502_after_discord_204_blocks_next_post_until_reconciled(self):
        webhook_calls = []
        def post(url, **kwargs):
            webhook_calls.append(kwargs["json"]["embeds"][0]["url"])
            self.assertEqual(self.state()["unconfirmed_discord_video_id"], VID)
            return SimpleNamespace(status_code=204)
        def failed_put(kind, ids):
            raise transient()
        with self.assertRaises(HTTPError):
            self._run(remote_ids=set(), history_write=failed_put, post=post)
        self.assertEqual(webhook_calls, [f"https://youtu.be/{VID}"])
        self.assertEqual(self.state()["unconfirmed_discord_video_id"], VID)
        self.assertEqual([v["video_id"] for v in self.state()["pending_videos"]], [VID])
        with patch.object(reaction, "read_ids", return_value=set()), patch.object(
            reaction.requests, "post"
        ) as sender, patch.object(reaction, "get_reactions_with_stats") as scan:
            with self.assertRaisesRegex(RuntimeError, "Unreconciled Discord delivery attempt"):
                reaction.run()
            sender.assert_not_called()
            scan.assert_not_called()

    def test_remote_ledger_reconciliation_clears_marker_without_replay(self):
        reaction.mark_discord_attempt(VID)
        with patch.object(reaction, "read_ids", side_effect=[
            {VID}, set()
        ]), patch.object(reaction, "get_reactions_with_stats",
                        return_value=([candidate()], datetime.now(timezone.utc))
        ), patch.object(reaction.requests, "post") as sender:
            self.assertEqual(reaction.run(), [])
        self.assertNotIn("unconfirmed_discord_video_id", self.state())
        sender.assert_not_called()

    def test_explicit_discord_429_clears_marker_without_marking_delivered(self):
        with self.assertRaisesRegex(RuntimeError, "Discord HTTP 429"):
            self._run(remote_ids=set(), history_write=lambda *a: None,
                      post=lambda *a, **k: SimpleNamespace(status_code=429))
        self.assertNotIn("unconfirmed_discord_video_id", self.state())
        self.assertEqual([v["video_id"] for v in self.state()["pending_videos"]], [VID])

    def test_timeout_keeps_marker_for_manual_reconciliation(self):
        with self.assertRaisesRegex(RuntimeError, "outcome uncertain"):
            self._run(remote_ids=set(), history_write=lambda *a: None,
                      post=lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
        self.assertEqual(self.state()["unconfirmed_discord_video_id"], VID)

    def test_corrupt_marker_refuses_to_send(self):
        state = self.state()
        state["unconfirmed_discord_video_id"] = {"id": VID}
        self.path.write_text(json.dumps(state), encoding="utf-8")
        with patch.object(reaction, "read_ids", return_value=set()), patch.object(
            reaction.requests, "post"
        ) as post:
            with self.assertRaisesRegex(ValueError, "Invalid unconfirmed"):
                reaction.run()
        post.assert_not_called()

    def test_no_marker_written_in_dry_run(self):
        with patch.object(reaction, "DRY_RUN", True), patch.object(
            reaction, "read_ids", return_value=set()
        ), patch.object(reaction, "get_reactions_with_stats",
                        return_value=([candidate()], datetime.now(timezone.utc))
        ), patch.object(reaction.requests, "post") as post:
            self.assertEqual(reaction.run(), [])
            post.assert_not_called()
        self.assertNotIn("unconfirmed_discord_video_id", self.state())


class SanitizedHistoryErrorTests(unittest.TestCase):
    def test_replayed_502_trace_is_classified_as_github_history_not_youtube(self):
        snippet = (
            "Traceback (most recent call last):\n"
            '  File "notification_history.py", line 98, in save_ids\n'
            '    _api_request("PUT", "contents/history/reactions.json", body)\n'
            "urllib.error.HTTPError: HTTP Error 502: Bad Gateway\n"
        )
        result = classify_error(snippet, "YouTube Reaction Tracker")
        self.assertEqual(result[0], "GitHub delivery-history service temporarily unavailable")
        self.assertNotIn("youtube", result[1].lower())

    def test_unresolved_marker_explains_manual_review_not_retrying_webhook(self):
        result = classify_error(
            "RuntimeError: Unreconciled Discord delivery attempt: "
            "verify the Discord post and confirmed-ID history",
            "YouTube Reaction Tracker")
        self.assertEqual(result[0], "Discord notification acknowledgement needs review")
        self.assertIn("blocked", result[1].lower())
        self.assertNotIn("github.com", result[1].lower())


if __name__ == "__main__":
    unittest.main()
