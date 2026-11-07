"""Offline registry-integrity regression tests.

No real YouTube, GitHub, Discord, secrets or production-registry writes.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as reactions
import discover_channels as discovery
from tracked_channel_registry import (
    RegistryIntegrityError, read_registry, write_registry,
)

FIRST = "UCExistingReactorChannel0001"
SECOND = "UCExistingReactorChannel0002"
EXCLUDED = "UCtEs-cB4K1XFnXgLrIWW1Zg"


def sample_registry():
    return {
        "channels": {
            FIRST: {
                "title": "Established Reactor",
                "added_at": "2026-10-06T00:00:00Z",
                "last_seen_at": "unparseable-legacy-date",
                "dormant": True,
                "discovery_videos": [{
                    "video_id": "sourceid001",
                    "published_at": "2026-11-05T00:00:00Z",
                    "discovered_at": "2026-11-05T01:00:00Z",
                }],
                "custom_legacy_field": {"preserve": True},
            },
            SECOND: "Legacy Reactor",
        },
        "last_discovery_run": "2026-11-05T00:00:00Z",
        "future_registry_metadata": "retain",
    }


class RegistryReadIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "tracked_channels.json"

    def put(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_modern_and_legacy_records_and_extra_fields_preserved(self):
        original = sample_registry()
        self.put(original)
        self.assertEqual(read_registry(self.path), original)

    def test_missing_file_fails_closed(self):
        with self.assertRaisesRegex(RegistryIntegrityError, "missing"):
            read_registry(self.path)

    def test_corrupt_json_fails_closed(self):
        self.path.write_text('{"channels": [', encoding="utf-8")
        with self.assertRaisesRegex(RegistryIntegrityError, "corrupt"):
            read_registry(self.path)

    def test_root_array_fails_closed(self):
        self.put([{"channels": {FIRST: {"title": "Reactor"}}}])
        with self.assertRaisesRegex(RegistryIntegrityError, "JSON object"):
            read_registry(self.path)

    def test_missing_or_empty_channels_fail_closed(self):
        for data in ({}, {"channels": {}}, {"channels": []},
                     {"channels": None}):
            with self.subTest(data=data):
                self.put(data)
                with self.assertRaisesRegex(RegistryIntegrityError, "nonempty channels"):
                    read_registry(self.path)

    def test_invalid_record_type_and_blank_title_fail_closed(self):
        for record in (None, [], 123, {}, {"title": ""}, " "):
            with self.subTest(record=record):
                self.put({"channels": {FIRST: record}})
                with self.assertRaises(RegistryIntegrityError):
                    read_registry(self.path)

    def test_bad_last_run_metadata_fails_closed(self):
        self.put({"channels": {FIRST: "Established"}, "last_discovery_run": []})
        with self.assertRaisesRegex(RegistryIntegrityError, "discovery bookmark"):
            read_registry(self.path)


class RegistryWriteIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "tracked_channels.json"

    def put(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_atomic_save_preserves_legacy_and_registry_metadata(self):
        before = sample_registry()
        self.put(before)
        changed = copy.deepcopy(before["channels"])
        changed[FIRST]["title"] = "Established Reactor (new name)"
        write_registry(self.path, changed,
                       last_discovery_run="2026-11-07T00:00:00Z")
        after = read_registry(self.path)
        self.assertEqual(after["channels"][SECOND], "Legacy Reactor")
        self.assertEqual(after["channels"][FIRST]["custom_legacy_field"],
                         {"preserve": True})
        self.assertEqual(after["future_registry_metadata"], "retain")
        self.assertEqual(after["last_discovery_run"], "2026-11-07T00:00:00Z")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_missing_existing_file_cannot_be_initialized_by_scheduled_save(self):
        with self.assertRaisesRegex(RegistryIntegrityError, "missing"):
            write_registry(self.path, {FIRST: "Reactor"},
                           last_discovery_run="2026-11-07T00:00:00Z")
        self.assertFalse(self.path.exists())

    def test_corrupt_original_cannot_be_overwritten(self):
        self.path.write_text('{"channels": {', encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RegistryIntegrityError, "corrupt"):
            write_registry(self.path, {FIRST: "Reactor"},
                           last_discovery_run="2026-11-07T00:00:00Z")
        self.assertEqual(self.path.read_bytes(), before)

    def test_refuses_to_replace_registry_with_empty_or_invalid_channels(self):
        self.put(sample_registry())
        before = self.path.read_bytes()
        for proposed in ({}, {FIRST: {"title": None}}):
            with self.subTest(proposed=proposed):
                with self.assertRaises(RegistryIntegrityError):
                    write_registry(self.path, proposed,
                                   last_discovery_run="2026-11-07T00:00:00Z")
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_replace_keeps_original_intact_and_cleans_temp(self):
        self.put(sample_registry())
        before = self.path.read_bytes()
        with patch("tracked_channel_registry.os.replace",
                   side_effect=OSError("replace unavailable")):
            with self.assertRaises(OSError):
                write_registry(self.path, sample_registry()["channels"],
                               last_discovery_run="2026-11-07T00:00:00Z")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_file_removed_after_read_refuses_scheduled_save(self):
        self.put(sample_registry())
        self.path.unlink()
        with self.assertRaisesRegex(RegistryIntegrityError, "missing"):
            write_registry(self.path, sample_registry()["channels"],
                           last_discovery_run="2026-11-07T00:00:00Z")


class IntegratedRegistrySafetyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "tracked_channels.json"

    def test_reaction_scan_refuses_corrupt_registry_before_youtube_and_discord(self):
        self.path.write_text('{"channels": [', encoding="utf-8")
        with patch.object(reactions, "TRACKED_CHANNELS_FILE", str(self.path)), patch.object(
            reactions, "read_ids", return_value=set()
        ), patch.object(reactions, "youtube") as yt, patch.object(
            reactions.requests, "post") as post, patch.object(
            reactions, "save_notification_queue") as queue, patch.object(
            reactions, "restore_durable_queue"):
            with self.assertRaises(RegistryIntegrityError):
                reactions.run()
        yt.playlistItems.assert_not_called()
        yt.videos.assert_not_called()
        post.assert_not_called()
        queue.assert_not_called()

    def test_discovery_refuses_missing_registry_before_search_or_commit(self):
        with patch.object(discovery, "TRACKED_CHANNELS_FILE", str(self.path)), patch.object(
            discovery, "youtube") as yt, patch.object(
            discovery, "save_tracked_channels") as save:
            with self.assertRaisesRegex(RegistryIntegrityError, "missing"):
                discovery.run_discovery()
        yt.search.assert_not_called()
        save.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_discovery_refuses_corrupt_registry_without_overwriting_it(self):
        self.path.write_text('{"channels": [', encoding="utf-8")
        before = self.path.read_bytes()
        with patch.object(discovery, "TRACKED_CHANNELS_FILE", str(self.path)), patch.object(
            discovery, "youtube") as yt:
            with self.assertRaises(RegistryIntegrityError):
                discovery.run_discovery()
        yt.search.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_reaction_loader_keeps_exact_id_exclusion(self):
        data = sample_registry()
        data["channels"][EXCLUDED] = {"title": "Excluded Channel"}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(reactions, "TRACKED_CHANNELS_FILE", str(self.path)):
            result = reactions.load_tracked_channels()
        self.assertEqual(set(result), {FIRST, SECOND})


if __name__ == "__main__":
    unittest.main()
