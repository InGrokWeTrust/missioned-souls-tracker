"""Regression tests for intermittent GitHub Contents read-after-write inconsistency.

Offline-only: simulate successful SHA-guarded GitHub PUT followed by stale
branch reads. No GitHub API, YouTube API, or Discord webhook is contacted.
"""
import base64
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import notification_history as history
import autoreactions as tracker

PREVIOUS = {
    "version": 1,
    "pending_videos": [],
    "held_videos": [{"video_id": "waiting00001",
                     "channel_id": "UCRecoverTest",
                     "published_at": "2026-11-10T19:00:00Z"}],
    "last_published_at": "2026-11-10T19:00:00Z",
    "last_complete_scan_at": "2026-11-10T19:30:00+00:00",
}
NEW = {**PREVIOUS, "last_complete_scan_at": "2026-11-10T20:30:00+00:00"}


def github_error(status):
    return HTTPError("https://example.invalid/github", status,
                     "mocked GitHub error", {}, io.BytesIO())


class DelayedReadbackGitHub:
    """In-memory GitHub Contents API with immutable commit views.

    The branch ref can temporarily return the previous file after PUT. A
    commit SHA identifies the exact new snapshot, independent of branch lag.
    """
    def __init__(self, *, stale_branch_reads=0, pinned_failures=0,
                 pinned_wrong_content=False, omit_commit=False):
        self.state = {"version": 1, "sent_video_ids": ["previousDelivered"],
                      "reaction_queue": copy.deepcopy(PREVIOUS),
                      "unrelated_field": "preserve"}
        self.previous = None
        self.revision = 1
        self.immutable = {}
        self.stale_branch_reads = stale_branch_reads
        self.pinned_failures = pinned_failures
        self.pinned_wrong_content = pinned_wrong_content
        self.omit_commit = omit_commit
        self.put_count = 0
        self.branch_get_count = 0
        self.pinned_refs = []

    def response(self, state, revision):
        payload = json.dumps(state, indent=2).encode()
        return {"type": "file", "sha": "blob-" + str(revision),
                "content": base64.b64encode(payload).decode()}

    def request(self, method, path, payload=None):
        if not path.startswith("contents/history/reactions.json"):
            raise AssertionError("Unexpected GitHub API path " + path)
        if method == "GET":
            ref = path.split("?ref=", 1)[1]
            if ref == "notification-history":
                self.branch_get_count += 1
                if self.previous is not None and self.stale_branch_reads:
                    self.stale_branch_reads -= 1
                    return self.response(self.previous, self.revision - 1)
                return self.response(self.state, self.revision)
            self.pinned_refs.append(ref)
            if self.pinned_failures:
                self.pinned_failures -= 1
                raise github_error(404)
            if ref not in self.immutable:
                raise github_error(404)
            if self.pinned_wrong_content:
                return self.response(self.previous, self.revision - 1)
            return self.response(*self.immutable[ref])
        if method != "PUT":
            raise AssertionError("Unexpected GitHub API method")
        if payload["branch"] != "notification-history":
            raise AssertionError("Wrong destination branch")
        if payload["sha"] != "blob-" + str(self.revision):
            raise github_error(409)
        self.previous = copy.deepcopy(self.state)
        self.state = json.loads(base64.b64decode(payload["content"]))
        self.revision += 1
        self.put_count += 1
        commit_sha = f"{self.revision:040x}"
        self.immutable[commit_sha] = (copy.deepcopy(self.state), self.revision)
        result = {"content": {"sha": "blob-" + str(self.revision)}}
        if not self.omit_commit:
            result["commit"] = {"sha": commit_sha}
        return result


class QueueReadbackConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bookmark = Path(self.temp.name) / "last_run.json"
        self.bookmark.write_text(json.dumps({
            "pending_videos": PREVIOUS["pending_videos"],
            "held_videos": PREVIOUS["held_videos"],
            "last_published_at": PREVIOUS["last_published_at"],
            "last_complete_scan_at": PREVIOUS["last_complete_scan_at"],
        }), encoding="utf-8")
        for p in (
            patch.object(history, "HISTORY_ENABLED", True),
            patch.object(tracker, "DRY_RUN", False),
            patch.object(tracker, "LAST_RUN_FILE", str(self.bookmark)),
            patch.dict(os.environ, {"DRY_RUN": "false"}),
        ):
            p.start()
            self.addCleanup(p.stop)

    def run_save(self, fake):
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep") as sleep:
            result = history.save_reaction_queue(copy.deepcopy(NEW))
        return result, sleep

    def test_reproduces_november_11_stale_branch_read_after_successful_put(self):
        fake = DelayedReadbackGitHub(stale_branch_reads=2)
        result, sleep = self.run_save(fake)
        self.assertEqual(result, history._validate_reaction_queue(NEW))
        self.assertEqual(fake.put_count, 1)
        self.assertEqual(fake.state["reaction_queue"], NEW)
        self.assertEqual(len(fake.pinned_refs), 1)
        self.assertEqual(fake.pinned_refs[0], f"{fake.revision:040x}")
        self.assertEqual(fake.state["sent_video_ids"], ["previousDelivered"])

    def test_pinned_commit_temporarily_404_then_verifies_after_bounded_retry(self):
        fake = DelayedReadbackGitHub(stale_branch_reads=3, pinned_failures=1)
        result, sleep = self.run_save(fake)
        self.assertEqual(result, history._validate_reaction_queue(NEW))
        self.assertEqual(fake.put_count, 1)
        self.assertEqual(len(fake.pinned_refs), 2)
        sleep.assert_called_once()

    def test_permanent_pinned_read_failure_keeps_local_artifact_unchanged(self):
        fake = DelayedReadbackGitHub(stale_branch_reads=10, pinned_failures=30)
        before = self.bookmark.read_bytes()
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep"), patch.object(tracker.requests, "post") as webhook:
            with self.assertRaises(RuntimeError):
                tracker.save_complete_scan(
                    __import__("datetime").datetime(2026, 11, 10, 20, 30,
                                                   tzinfo=__import__("datetime").timezone.utc))
        self.assertEqual(fake.put_count, 1)
        self.assertEqual(self.bookmark.read_bytes(), before)
        webhook.assert_not_called()

    def test_pinned_commit_content_mismatch_fails_closed(self):
        fake = DelayedReadbackGitHub(stale_branch_reads=5, pinned_wrong_content=True)
        with patch.object(history, "_api_request", side_effect=fake.request), patch.object(
            history.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "not verified"):
                history.save_reaction_queue(copy.deepcopy(NEW))
        self.assertEqual(fake.put_count, 1)
        self.assertTrue(fake.pinned_refs)

    def test_missing_commit_identity_fails_closed_without_branch_fallback(self):
        fake = DelayedReadbackGitHub(omit_commit=True)
        with patch.object(history, "_api_request", side_effect=fake.request):
            with self.assertRaisesRegex(RuntimeError, "commit"):
                history.save_reaction_queue(copy.deepcopy(NEW))
        self.assertEqual(fake.put_count, 1)
        self.assertEqual(fake.pinned_refs, [])

    def test_immediate_consistent_success_preserves_one_write(self):
        fake = DelayedReadbackGitHub()
        result, sleep = self.run_save(fake)
        self.assertEqual(result["last_complete_scan_at"],
                         "2026-11-10T20:30:00+00:00")
        self.assertEqual(fake.put_count, 1)
        self.assertEqual(len(fake.pinned_refs), 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
