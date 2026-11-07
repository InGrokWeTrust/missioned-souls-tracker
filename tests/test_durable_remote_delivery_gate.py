"""Offline protocol tests: remote Discord reservation survives a lost runner.

All GitHub Contents responses below are simulated in memory. No actual
network request or webhook is made. Durable history remains version 1.
"""
import base64
import io
import json
import os
import unittest
from copy import deepcopy
from urllib.error import HTTPError
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import notification_history as history

VID = "durableA001"
OTHER = "durableB002"
MARKER = "unconfirmed_discord_video_id"


def error(status):
    return HTTPError("https://example.invalid/github", status, "simulated error", {}, io.BytesIO())


class FakeGitHubContents:
    """Mimics SHA-guarded version-1 ledgers in the notification-history branch."""
    def __init__(self):
        self.files = {
            kind: {"version": 1, "sent_video_ids": []}
            for kind in ("reactions", "live")
        }
        self.revisions = {kind: 1 for kind in self.files}
        self.puts = []
        self.fail_after_put = []
        self.fail_before_put = []
        self.fail_get = []

    def request(self, method, path, payload=None):
        if path.startswith("contents/history/reactions.json"):
            kind = "reactions"
        elif path.startswith("contents/history/live.json"):
            kind = "live"
        else:
            raise AssertionError(f"Unknown fake GitHub API route: {path}")
        if method == "GET":
            if self.fail_get:
                raise error(self.fail_get.pop(0))
            data = json.dumps(self.files[kind], indent=2).encode()
            return {"type": "file", "sha": f"sha-{self.revisions[kind]}",
                    "content": base64.b64encode(data).decode()}
        if method != "PUT":
            raise AssertionError(f"Unsupported GitHub call: {method}")
        if self.fail_before_put:
            raise error(self.fail_before_put.pop(0))
        if payload["branch"] != "notification-history":
            raise AssertionError("Wrong history branch")
        if payload["sha"] != f"sha-{self.revisions[kind]}":
            raise error(409)
        data = json.loads(base64.b64decode(payload["content"]))
        self.files[kind] = data
        self.revisions[kind] += 1
        self.puts.append((kind, deepcopy(data)))
        if self.fail_after_put:
            raise error(self.fail_after_put.pop(0))
        return {"content": {"sha": f"sha-{self.revisions[kind]}"}}


class DurableRemoteGateTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHubContents()
        patcher = patch.object(history, "_api_request", side_effect=self.fake.request)
        patcher.start()
        self.addCleanup(patcher.stop)
        p = patch.object(history, "HISTORY_ENABLED", True)
        p.start()
        self.addCleanup(p.stop)
        q = patch.dict(os.environ, {"DRY_RUN": "false"})
        q.start()
        self.addCleanup(q.stop)

    def test_prepost_remote_marker_and_single_atomic_confirm_put(self):
        self.assertEqual(history.reserve_delivery("reactions", VID), VID)
        self.assertEqual(history.read_ids("live"), set())
        self.assertEqual(self.fake.files["reactions"][MARKER], VID)
        with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
            history.read_ids("reactions")
        self.assertEqual(history.confirm_delivery("reactions", VID), [VID])
        self.assertEqual(self.fake.files["reactions"],
                         {"version": 1, "sent_video_ids": [VID]})
        self.assertEqual([item[1].get(MARKER) for item in self.fake.puts],
                         [VID, None])
        self.assertEqual(history.read_ids("reactions"), {VID})

    def test_crash_after_discord_before_confirm_cannot_resend_from_new_runner(self):
        history.reserve_delivery("reactions", VID)
        # Simulated runner destruction: no LAST_RUN_FILE or local attempt exists.
        with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
            history.read_ids("reactions")
        with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
            history.reserve_delivery("reactions", VID)
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [])
        self.assertEqual(self.fake.files["reactions"][MARKER], VID)

    def test_other_workflow_is_blocked_by_unresolved_reaction_attempt(self):
        history.reserve_delivery("reactions", VID)
        with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
            history.reserve_delivery("live", OTHER)
        self.assertEqual(self.fake.files["live"]["sent_video_ids"], [])
        self.assertNotIn(MARKER, self.fake.files["live"])

    def test_other_workflow_cannot_post_same_confirmed_video(self):
        history.reserve_delivery("live", VID)
        history.confirm_delivery("live", VID)
        with self.assertRaisesRegex(RuntimeError, "already durably confirmed"):
            history.reserve_delivery("reactions", VID)

    def test_ambiguous_prepost_put_applied_blocks_post_and_future_runs(self):
        self.fake.fail_after_put.append(502)
        with self.assertRaises(HTTPError) as err:
            history.reserve_delivery("reactions", VID)
        self.assertEqual(err.exception.code, 502)
        self.assertEqual(self.fake.files["reactions"][MARKER], VID)
        with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
            history.read_ids("reactions")

    def test_ambiguous_confirmation_put_that_committed_is_reconciled(self):
        history.reserve_delivery("reactions", VID)
        self.fake.fail_after_put.append(502)
        with patch.object(history.time, "sleep") as sleep:
            result = history.confirm_delivery("reactions", VID)
        self.assertEqual(result, [VID])
        self.assertEqual(self.fake.files["reactions"],
                         {"version": 1, "sent_video_ids": [VID]})
        sleep.assert_called_once_with(1)
        self.assertEqual(len(self.fake.puts), 2)

    def test_repeated_uncommitted_github_502_leaves_remote_marker(self):
        history.reserve_delivery("reactions", VID)
        self.fake.fail_before_put[:] = [502] * history.MAX_HISTORY_WRITE_ATTEMPTS
        with patch.object(history.time, "sleep"):
            with self.assertRaises(HTTPError):
                history.confirm_delivery("reactions", VID)
        self.assertEqual(self.fake.files["reactions"][MARKER], VID)
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [])
        with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
            history.reserve_delivery("live", OTHER)

    def test_explicit_rejection_removes_marker_without_claiming_delivery(self):
        history.reserve_delivery("live", VID)
        self.assertEqual(history.reject_delivery("live", VID), [])
        self.assertEqual(self.fake.files["live"]["sent_video_ids"], [])
        self.assertNotIn(MARKER, self.fake.files["live"])
        self.assertEqual(history.reserve_delivery("live", VID), VID)

    def test_ambiguous_rejection_commit_is_reconciled_safely(self):
        history.reserve_delivery("live", VID)
        self.fake.fail_after_put.append(502)
        with patch.object(history.time, "sleep"):
            self.assertEqual(history.reject_delivery("live", VID), [])
        self.assertNotIn(MARKER, self.fake.files["live"])
        self.assertEqual(self.fake.files["live"]["sent_video_ids"], [])

    def test_confirm_without_matching_reservation_never_fabricates_delivery(self):
        with self.assertRaisesRegex(RuntimeError, "No matching"):
            history.confirm_delivery("reactions", VID)
        self.assertEqual(history.read_ids("reactions"), set())

    def test_wrong_marker_id_cannot_be_cleared_by_other_id(self):
        history.reserve_delivery("reactions", VID)
        with self.assertRaisesRegex(RuntimeError, "Different remote"):
            history.confirm_delivery("reactions", OTHER)
        with self.assertRaisesRegex(RuntimeError, "Different remote"):
            history.reject_delivery("reactions", OTHER)
        self.assertEqual(self.fake.files["reactions"][MARKER], VID)

    def test_old_save_ids_cannot_overwrite_unresolved_attempt(self):
        history.reserve_delivery("live", VID)
        with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
            history.save_ids("live", [OTHER])
        self.assertEqual(self.fake.files["live"][MARKER], VID)
        self.assertEqual(self.fake.files["live"]["sent_video_ids"], [])

    def test_invalid_marker_in_remote_file_fails_closed(self):
        self.fake.files["reactions"][MARKER] = {"id": VID}
        with self.assertRaises(ValueError):
            history.read_ids("reactions")
        with self.assertRaises(ValueError):
            history.reserve_delivery("live", OTHER)
        self.assertEqual(len(self.fake.puts), 0)

    def test_no_durable_mutations_when_dry_run(self):
        with patch.dict(os.environ, {"DRY_RUN": "true"}):
            for action in (
                lambda: history.reserve_delivery("reactions", VID),
                lambda: history.confirm_delivery("reactions", VID),
                lambda: history.reject_delivery("reactions", VID),
            ):
                with self.assertRaisesRegex(RuntimeError, "DRY RUN"):
                    action()
        self.assertEqual(self.fake.puts, [])

    def test_github_403_during_reserve_refuses_prepost_confirmation(self):
        self.fake.fail_before_put.append(403)
        with self.assertRaises(HTTPError) as err:
            history.reserve_delivery("reactions", VID)
        self.assertEqual(err.exception.code, 403)
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [])
        self.assertNotIn(MARKER, self.fake.files["reactions"])


import tempfile
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace

import autoreactions as reaction
import check_live_streams as live


def reaction_vod():
    return {
        "video_id": VID, "channel_id": "UCSimulated", "channel": "Test Reactor",
        "title": "Missioned Souls - song REACTION",
        "published_at": "2026-11-05T16:00:00Z",
        "url": "https://youtu.be/" + VID,
        "thumbnail": "https://i.ytimg.com/vi/" + VID + "/hqdefault.jpg",
        "live_broadcast_content": "none",
        "live_streaming_details": None, "metadata_confirmed": True,
        "duration_sec": 600, "view_count": 4, "like_count": 1,
        "comment_count": 0,
    }


def live_video():
    return {
        "video_id": VID, "channel_id": "UCSimulated", "channel": "Test Reactor",
        "title": "Missioned Souls live show",
        "published_at": "2026-11-05T16:00:00Z",
        "url": "https://youtu.be/" + VID,
        "thumbnail": "https://i.ytimg.com/vi/" + VID + "/hqdefault.jpg",
        "live_broadcast_content": "live",
    }


class EndToEndSenderOrderingTests(unittest.TestCase):
    """Run real sender/remote functions against a mocked GitHub Contents API."""

    def setUp(self):
        self.fake = FakeGitHubContents()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bookmark = Path(tmp.name) / "last_run.json"
        self.live_bookmark = Path(tmp.name) / "live_last_run.json"
        self.live_bookmark.write_text('{"sent_video_ids": []}', encoding="utf-8")
        patches = [
            patch.object(history, "_api_request", side_effect=self.fake.request),
            patch.object(history, "HISTORY_ENABLED", True),
            patch.dict(os.environ, {"DRY_RUN": "false"}),
            patch.object(reaction, "DRY_RUN", False),
            patch.object(live, "DRY_RUN", False),
            patch.object(reaction, "LAST_RUN_FILE", str(self.bookmark)),
            patch.object(live, "LIVE_LAST_RUN_FILE", str(self.live_bookmark)),
            patch.object(reaction.time, "sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_reaction_sender_reserves_remotely_before_post_and_confirms_once(self):
        def accepted(*args, **kwargs):
            self.assertEqual(self.fake.files["reactions"].get(MARKER), VID)
            self.assertEqual(json.loads(self.bookmark.read_text())[reaction.UNCERTAIN_DELIVERY_KEY], VID)
            return SimpleNamespace(status_code=204)
        with patch.object(reaction.requests, "post", side_effect=accepted) as post:
            sent = reaction.send_to_discord(
                [reaction_vod()], max_to_send=1,
                before_post=reaction.mark_discord_attempt,
                after_confirmed=reaction.clear_discord_attempt,
                after_rejected=reaction.clear_discord_attempt,
            )
        self.assertEqual([v["video_id"] for v in sent], [VID])
        post.assert_called_once()
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [VID])
        self.assertNotIn(MARKER, self.fake.files["reactions"])
        self.assertNotIn(reaction.UNCERTAIN_DELIVERY_KEY,
                         json.loads(self.bookmark.read_text()))

    def test_runner_crash_after_reaction_discord_204_survives_lost_artifact(self):
        with patch.object(reaction.requests, "post",
                          return_value=SimpleNamespace(status_code=204)) as first_post, patch.object(
            reaction, "confirm_delivery", side_effect=SystemExit(17)):
            with self.assertRaises(SystemExit):
                reaction.send_to_discord(
                    [reaction_vod()], max_to_send=1,
                    before_post=reaction.mark_discord_attempt,
                    after_confirmed=reaction.clear_discord_attempt,
                    after_rejected=reaction.clear_discord_attempt)
        first_post.assert_called_once()
        self.assertEqual(self.fake.files["reactions"].get(MARKER), VID)
        self.bookmark.unlink()  # Runner artifact deleted; remote state survives.
        with patch.object(reaction, "get_reactions_with_stats") as scan, patch.object(
            reaction.requests, "post") as second_post:
            with self.assertRaisesRegex(RuntimeError, "Unreconciled durable"):
                reaction.run()
        scan.assert_not_called()
        second_post.assert_not_called()

    def test_live_sender_reserves_remotely_before_post_and_confirms_once(self):
        def accepted(*args, **kwargs):
            self.assertEqual(self.fake.files["live"].get(MARKER), VID)
            self.assertEqual(json.loads(self.live_bookmark.read_text())[
                live.UNCERTAIN_LIVE_DELIVERY_KEY], VID)
            return SimpleNamespace(status_code=204)
        with patch.object(live.requests, "post", side_effect=accepted) as post:
            sent = live.send_to_discord([live_video()])
        self.assertEqual([v["video_id"] for v in sent], [VID])
        post.assert_called_once()
        self.assertEqual(self.fake.files["live"]["sent_video_ids"], [VID])
        self.assertNotIn(MARKER, self.fake.files["live"])
        self.assertNotIn(live.UNCERTAIN_LIVE_DELIVERY_KEY,
                         json.loads(self.live_bookmark.read_text()))

    def test_runner_crash_after_live_discord_204_survives_lost_artifact(self):
        with patch.object(live.requests, "post",
                          return_value=SimpleNamespace(status_code=204)) as first_post, patch.object(
            live, "confirm_delivery", side_effect=SystemExit(17)):
            with self.assertRaises(SystemExit):
                live.send_to_discord([live_video()])
        first_post.assert_called_once()
        self.assertEqual(self.fake.files["live"].get(MARKER), VID)
        self.live_bookmark.unlink()  # Runner artifact deleted; remote state survives.
        with patch.object(live, "find_active_live_streams") as scan, patch.object(
            live.requests, "post") as second_post:
            with self.assertRaisesRegex(RuntimeError, "Unreconciled durable"):
                live.run()
        scan.assert_not_called()
        second_post.assert_not_called()

    def test_live_non204_releases_remote_and_local_without_confirming_id(self):
        with patch.object(live.requests, "post",
                          return_value=SimpleNamespace(status_code=429)) as post:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                live.send_to_discord([live_video()])
        post.assert_called_once()
        self.assertEqual(self.fake.files["live"]["sent_video_ids"], [])
        self.assertNotIn(MARKER, self.fake.files["live"])
        self.assertNotIn(live.UNCERTAIN_LIVE_DELIVERY_KEY,
                         json.loads(self.live_bookmark.read_text()))


if __name__ == "__main__":
    unittest.main()
