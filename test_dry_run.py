"""Dry-run regression checks: never post, never mutate saved bookmarks."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_delivery import video, NOW, blank_state
import autoreactions as reactions
import check_live_streams as live


class DryRunTests(unittest.TestCase):
    def test_reaction_dry_run_does_not_send_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory, "last_run.json")
            original = blank_state()
            original["seen_video_ids"] = ["Etztcp21KUw"]
            saved.write_text(json.dumps(original), encoding="utf-8")
            old_bytes = saved.read_bytes()
            with patch.object(reactions, "DRY_RUN", True), \
                 patch.object(reactions, "LAST_RUN_FILE", str(saved)), \
                 patch.object(reactions, "LIVE_LAST_RUN_FILE", str(Path(directory, "missing-live.json"))), \
                 patch.object(reactions, "send_to_discord") as send:
                result = reactions.run_tracker([video(1)], {"UC1"}, run_start=NOW)
            self.assertEqual(result, [])
            self.assertEqual(saved.read_bytes(), old_bytes)
            send.assert_not_called()

    def test_direct_reaction_sender_never_posts(self):
        with patch.object(reactions, "DRY_RUN", True), \
             patch.object(reactions, "post_webhook") as post:
            self.assertEqual(reactions.send_to_discord([video(1)]), [])
            post.assert_not_called()

    def test_live_dry_run_never_sends_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory, "live_last_run.json")
            saved.write_text('{"sent_video_ids":["already"],"last_check":"previous"}', encoding="utf-8")
            old_bytes = saved.read_bytes()
            with patch.object(live, "DRY_RUN", True), \
                 patch.object(live, "LIVE_LAST_RUN_FILE", str(saved)), \
                 patch.object(live, "post_webhook") as post:
                self.assertEqual(live.send_to_discord([video(1)]), [])
                live.save_state(["different"])
                post.assert_not_called()
            self.assertEqual(saved.read_bytes(), old_bytes)


if __name__ == "__main__":
    unittest.main()
