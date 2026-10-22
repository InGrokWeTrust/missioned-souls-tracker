"""Offline checks for restoring confirmed legacy deliveries without losing state."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from reaction_state import load_state, save_state, plan_notifications


class HistoricalBookmarkTests(unittest.TestCase):
    def test_restoration_merges_into_existing_bookmark_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "last_run.json")
            historical = json.load(open(os.path.join(
                os.path.dirname(__file__), "historical_deliveries.json"), encoding="utf-8"))
            self.assertIn("jm6K2OqOIWE", historical["confirmed_sent_video_ids"])
            existing = {
                "version": 2,
                "last_published_at": "2026-10-22T00:00:00Z",
                "seen_video_ids": ["Etztcp21KUw", "another-old-id"],
                "known_channel_ids": ["old-channel"],
                "pending": [{
                    "video_id": "pending-new-id", "channel_id": "old-channel",
                    "published_at": "2026-10-22T00:00:00Z",
                    "title": "New reaction", "url": "https://youtu.be/pending-new-id"
                }]
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f)
            state = load_state(path)
            for vid in historical["confirmed_sent_video_ids"]:
                if vid not in state["seen_video_ids"]:
                    state["seen_video_ids"].append(vid)
            state["pending"] = [v for v in state["pending"]
                                if v["video_id"] not in historical["confirmed_sent_video_ids"]]
            save_state(path, state)
            restored = load_state(path)
            self.assertEqual(restored["pending"], existing["pending"])
            self.assertEqual(restored["known_channel_ids"], existing["known_channel_ids"])
            self.assertTrue(set(existing["seen_video_ids"]).issubset(restored["seen_video_ids"]))
            self.assertIn("jm6K2OqOIWE", restored["seen_video_ids"])

            video = {
                "video_id": "jm6K2OqOIWE", "channel_id": "old-channel",
                "title": "Highway Star reaction", "published_at": "2026-10-22T08:50:00Z",
                "url": "https://youtu.be/jm6K2OqOIWE"
            }
            n, _ = plan_notifications(
                restored, [video], {"old-channel"},
                now=datetime(2026, 10, 22, 10, tzinfo=timezone.utc))
            self.assertEqual(n, 0)
            self.assertEqual(restored["pending"], existing["pending"])


if __name__ == "__main__":
    unittest.main()
