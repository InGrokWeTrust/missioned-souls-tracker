"""Offline durable reaction-queue recovery and atomic checkpoint tests.

Uses simulated GitHub Contents GET/PUT and mocked Discord/YouTube. Does not
touch real notification history, real channels, or production artifacts.
"""
import json
import os
import tempfile
import unittest
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
from test_durable_remote_delivery_gate import FakeGitHubContents, reaction_vod

VID = "durableA001"
HOLD = "waiting0012"
OLD = "2026-11-05T00:00:00Z"
SCAN = "2026-11-05T00:30:00Z"
PUBLISHED = "2026-11-05T16:00:00Z"


def queue_record(vid, stamp=PUBLISHED):
    return {"channel_id": "UCSimulated", "video_id": vid, "published_at": stamp}


def state(pending=None, held=None):
    return {
        "last_published_at": OLD,
        "last_complete_scan_at": SCAN,
        "pending_videos": pending if pending is not None else [queue_record(VID)],
        "held_videos": held if held is not None else [queue_record(HOLD)],
    }


class DurableQueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "last_run.json"
        self.path.write_text(json.dumps(state()), encoding="utf-8")
        self.fake = FakeGitHubContents()
        for p in (
            patch.object(history, "_api_request", side_effect=self.fake.request),
            patch.object(history, "HISTORY_ENABLED", True),
            patch.dict(os.environ, {"DRY_RUN": "false"}),
            patch.object(reaction, "DRY_RUN", False),
            patch.object(reaction, "LAST_RUN_FILE", str(self.path)),
            patch.object(reaction.time, "sleep"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def seeded(self):
        reaction.restore_durable_queue()
        return history.read_reaction_queue()

    def test_first_upgrade_seeds_existing_bookmark_without_replaying(self):
        before = self.path.read_bytes()
        remote = self.seeded()
        self.assertEqual(len(remote["pending_videos"]), 1)
        self.assertEqual(len(remote["held_videos"]), 1)
        self.assertEqual(remote["last_published_at"], OLD)
        self.assertEqual(remote["last_complete_scan_at"], SCAN)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [])

    def test_missing_artifact_is_rebuilt_from_remote_queue(self):
        self.seeded()
        self.path.unlink()
        reaction.restore_durable_queue()
        self.assertEqual(self.load(), state())

    def test_corrupted_artifact_is_rebuilt_from_existing_remote_queue(self):
        self.seeded()
        self.path.write_text('{"pending_videos": [', encoding="utf-8")
        reaction.restore_durable_queue()
        self.assertEqual(self.load(), state())

    def test_stale_bookmark_cannot_erase_existing_remote_queue(self):
        self.seeded()
        self.path.write_text(json.dumps(state(pending=[], held=[])), encoding="utf-8")
        reaction.restore_durable_queue()
        self.assertEqual(self.load()["pending_videos"], [queue_record(VID)])
        self.assertEqual(self.load()["held_videos"], [queue_record(HOLD)])

    def test_missing_artifact_before_first_remote_seed_fails_closed(self):
        self.path.unlink()
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            reaction.restore_durable_queue()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.fake.puts, [])

    def test_invalid_original_artifact_before_seed_fails_closed(self):
        self.path.write_text('{"pending_videos": [', encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            reaction.restore_durable_queue()
        self.assertEqual(self.fake.puts, [])

    def test_after_artifact_loss_ready_reaction_is_reconfirmed_and_sent_once(self):
        self.seeded()
        self.path.unlink()
        expected = reaction_vod()
        with patch.object(reaction, "get_reactions_with_stats",
                          return_value=([expected], datetime.now(timezone.utc))
        ) as scan, patch.object(reaction.requests, "post",
                               return_value=SimpleNamespace(status_code=204)) as webhook:
            result = reaction.run()
            self.assertEqual([x["video_id"] for x in result], [VID])
            self.assertEqual(reaction.run(), [])
        self.assertEqual(webhook.call_count, 1)
        self.assertEqual(self.fake.files["reactions"]["sent_video_ids"], [VID])
        self.assertEqual(history.read_reaction_queue()["pending_videos"], [])
        self.assertEqual(history.read_reaction_queue()["held_videos"], [queue_record(HOLD)])
        self.assertIsNotNone(history.read_reaction_queue()["last_complete_scan_at"])
        self.assertGreaterEqual(scan.call_count, 2)

    def test_remote_put_502_after_success_is_reconciled_without_second_put(self):
        self.fake.fail_after_put.append(502)
        with patch.object(history.time, "sleep") as sleep:
            result = self.seeded()
        self.assertEqual(result["pending_videos"], [queue_record(VID)])
        self.assertEqual(len(self.fake.puts), 1)
        sleep.assert_called_once_with(1)

    def test_remote_outage_blocks_bookmark_advance_without_local_mutation(self):
        self.seeded()
        before = self.path.read_bytes()
        self.fake.fail_before_put[:] = [502] * history.MAX_HISTORY_WRITE_ATTEMPTS
        with patch.object(history.time, "sleep"), patch.object(
            reaction.requests, "post") as webhook:
            with self.assertRaises(HTTPError):
                reaction.save_notification_queue(
                    {}, {VID: queue_record(VID), "added01": queue_record("added01")})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(history.read_reaction_queue()["pending_videos"],
                         [queue_record(VID)])
        webhook.assert_not_called()

    def test_duplicate_cross_queue_video_ids_rejected_before_remote_write(self):
        self.seeded()
        before = len(self.fake.puts)
        with self.assertRaisesRegex(RuntimeError, "both held and ready"):
            reaction.save_notification_queue({VID: queue_record(VID)},
                                             {VID: queue_record(VID)})
        self.assertEqual(len(self.fake.puts), before)

    def test_invalid_remote_queue_fails_before_state_recovery(self):
        self.fake.files["reactions"]["reaction_queue"] = {
            "version": 1, "pending_videos": [queue_record(VID), queue_record(VID)],
            "held_videos": [], "last_published_at": OLD,
        }
        with self.assertRaisesRegex(ValueError, "Duplicate video ID"):
            reaction.restore_durable_queue()

    def test_confirmed_video_ids_survive_queue_checkpoint(self):
        self.seeded()
        history.reserve_delivery("reactions", VID)
        history.confirm_delivery("reactions", VID)
        reaction.save_notification_queue({}, {VID: queue_record(VID)})
        self.assertEqual(history.read_ids("reactions"), {VID})
        self.assertEqual(history.read_reaction_queue()["pending_videos"], [queue_record(VID)])

    def test_legacy_confirmed_id_writer_preserves_durable_queue_metadata(self):
        queued = self.seeded()
        self.assertEqual(history.save_ids("reactions", ["legacySentA"]), ["legacySentA"])
        self.assertEqual(history.read_ids("reactions"), {"legacySentA"})
        self.assertEqual(history.read_reaction_queue(), queued)
        self.assertEqual(self.fake.files["reactions"]["reaction_queue"], queued)

    def test_unresolved_remote_delivery_marker_blocks_queue_update(self):
        self.seeded()
        history.reserve_delivery("reactions", VID)
        with self.assertRaisesRegex(RuntimeError, "Unreconciled"):
            reaction.save_notification_queue({}, {VID: queue_record(VID)})
        self.assertEqual(self.fake.files["reactions"]["unconfirmed_discord_video_id"], VID)

    def test_dry_run_does_not_mutate_remote_queue_or_artifact(self):
        before = self.path.read_bytes()
        with patch.object(reaction, "DRY_RUN", True), patch.dict(
            os.environ, {"DRY_RUN": "true"}):
            reaction.restore_durable_queue()
            reaction.save_notification_queue({}, {VID: queue_record(VID)})
        self.assertEqual(self.fake.puts, [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_bounded_queue_enforced(self):
        bad = state(pending=[queue_record(f"vid-{i}") for i in range(2001)])
        with self.assertRaises(ValueError):
            history._validate_reaction_queue(
                {"version": 1, **bad})


if __name__ == "__main__":
    unittest.main()
