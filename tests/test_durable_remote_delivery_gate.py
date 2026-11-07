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


if __name__ == "__main__":
    unittest.main()
