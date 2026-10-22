"""Offline append-only history tests, with GitHub API fully mocked."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import notification_history as history


class DurableHistoryTests(unittest.TestCase):
    def test_history_append_only_union(self):
        self.assertEqual(history.merge_ids(["old", "metallica"], ["old", "highway"]), ["old", "metallica", "highway"])
        with self.assertRaises(ValueError):
            history.merge_ids(["ok"], [None])

    def test_save_merges_remote_before_write(self):
        with patch.object(history, "HISTORY_ENABLED", True), \
             patch.object(history, "DRY_RUN", False), \
             patch.object(history, "_read_remote", return_value=(["earlier"], "sha-old")), \
             patch.object(history, "_api_request", return_value={}) as api:
            result = history.save_ids("reactions", ["new"])
        self.assertEqual(result, ["earlier", "new"])
        args = api.call_args.args
        self.assertEqual(args[0], "PUT")
        import base64
        payload = json.loads(base64.b64decode(args[2]["content"]))
        self.assertEqual(payload["sent_video_ids"], ["earlier", "new"])
        self.assertEqual(args[2]["branch"], history.HISTORY_BRANCH)
        self.assertEqual(args[2]["sha"], "sha-old")

    def test_save_never_removes_old_ids(self):
        with patch.object(history, "HISTORY_ENABLED", True), \
             patch.object(history, "DRY_RUN", False), \
             patch.object(history, "_read_remote", return_value=(["old", "new"], "sha")), \
             patch.object(history, "_api_request") as api:
            self.assertEqual(history.save_ids("reactions", ["new"]), ["old", "new"])
        api.assert_not_called()

    def test_hydrate_preserves_pending_and_recovers_confirmed_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "last_run.json")
            original = {
                "version": 2, "last_published_at": "2026-10-22T00:00:00Z",
                "seen_video_ids": ["Etztcp21KUw"], "known_channel_ids": ["channel"],
                "pending": [
                    {"video_id": "jm6K2OqOIWE", "title": "Highway Star"},
                    {"video_id": "new", "title": "New reaction"}
                ],
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            with patch.object(history, "HISTORY_ENABLED", True), \
                 patch.object(history, "DRY_RUN", False), \
                 patch.object(history, "_read_remote", return_value=(["jm6K2OqOIWE"], "sha")), \
                 patch.object(history, "save_ids") as save:
                ids = history.hydrate("reactions", path)
            self.assertEqual(ids, ["jm6K2OqOIWE", "Etztcp21KUw"])
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["pending"], [original["pending"][1]])
            self.assertEqual(result["known_channel_ids"], original["known_channel_ids"])
            save.assert_called_once_with("reactions", ids)

    def test_dry_run_never_modifies_local_or_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "live_last_run.json")
            path.write_text('{"sent_video_ids":["local"],"last_check":"old"}')
            before = path.read_bytes()
            with patch.object(history, "DRY_RUN", True), \
                 patch.object(history, "_read_remote", return_value=(["remote"], "sha")), \
                 patch.object(history, "save_ids") as save:
                ids = history.hydrate("live", path)
            self.assertEqual(ids, ["remote", "local"])
            self.assertEqual(path.read_bytes(), before)
            save.assert_not_called()

    def test_missing_and_corrupt_artifacts_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory, "none.json")
            with self.assertRaisesRegex(RuntimeError, "artifact unavailable"):
                history.hydrate("reactions", missing)
            missing.write_text('{"version":2,"pending":[],"seen_video_ids":"bad"}')
            with patch.object(history, "_read_remote", return_value=([], "sha")):
                with self.assertRaises(ValueError):
                    history.hydrate("reactions", missing)

    def test_remote_api_failure_prevents_hydration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "last_run.json")
            path.write_text('{"version":2,"pending":[],"seen_video_ids":[]}')
            original = path.read_bytes()
            with patch.object(history, "_read_remote", side_effect=RuntimeError("history unreachable")):
                with self.assertRaisesRegex(RuntimeError, "history unreachable"):
                    history.hydrate("reactions", path)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
