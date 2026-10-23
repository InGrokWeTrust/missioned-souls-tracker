"""Offline regression tests: keep search-dormant reactor channels.

Only the explicit exclusion rules may remove existing channels.
Absence from recent YouTube search results is not proof of inactivity.
No live API calls, Discord messages or GitHub production writes.
"""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/unused")
os.environ.setdefault("HISTORY_ENABLED", "true")

import discover_channels as discovery
import autoreactions as reaction
from channel_exclusions import EXCLUDED_CHANNEL_IDS


OLD = "UCOldReactor0000000000001"
OTHER = "UCOtherOld00000000000002"
FRESH = "UCNewReactor0000000000001"
HCJB = next(iter(EXCLUDED_CHANNEL_IDS))


def tracked_channel(name, seen):
    return {"title": name, "added_at": "2026-01-01T00:00:00Z",
            "last_seen_at": seen}


class PreserveInactiveChannelTests(unittest.TestCase):
    def test_absent_from_search_marks_dormant_without_deleting(self):
        now = datetime(2026, 10, 23, tzinfo=timezone.utc)
        records = {
            OLD: tracked_channel("Old Reactor", "2026-01-01T00:00:00Z"),
            OTHER: tracked_channel("Another Reactor", "2026-10-22T00:00:00Z"),
        }
        original_first = copy.deepcopy(records[OLD])
        count = discovery.mark_search_dormancy(records, now=now)
        self.assertEqual(count, 1)
        self.assertEqual(set(records), {OLD, OTHER})
        self.assertEqual(records[OLD]["last_seen_at"], original_first["last_seen_at"])
        self.assertEqual(records[OLD]["added_at"], original_first["added_at"])
        self.assertTrue(records[OLD]["dormant"])
        self.assertFalse(records[OTHER]["dormant"])

    def test_search_dormancy_threshold_is_not_upload_inactivity(self):
        now = datetime(2026, 10, 23, tzinfo=timezone.utc)
        records = {OLD: tracked_channel("Old", (now-timedelta(days=61)).isoformat()),
                   OTHER: tracked_channel("Other", (now-timedelta(days=60)).isoformat())}
        count = discovery.mark_search_dormancy(records, now=now)
        self.assertEqual(count, 1)
        self.assertTrue(records[OLD]["dormant"])
        self.assertFalse(records[OTHER]["dormant"])
        self.assertEqual(len(records), 2)

    def test_corrupt_and_legacy_last_seen_do_not_delete_reactor(self):
        now = datetime(2026, 10, 23, tzinfo=timezone.utc)
        records = {
            OLD: {"title": "Missing date"},
            OTHER: {"title": "Malformed date", "last_seen_at": "not-a-date"},
            FRESH: {"title": "Naive date", "last_seen_at": "2026-01-01T00:00:00"},
        }
        count = discovery.mark_search_dormancy(records, now=now)
        self.assertEqual(count, 1)
        self.assertEqual(len(records), 3)
        self.assertNotIn("dormant", records[OLD])
        self.assertNotIn("dormant", records[OTHER])
        self.assertTrue(records[FRESH]["dormant"])

    def test_dormant_tracked_channel_still_scanned_for_uploads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tracked_channels.json"
            path.write_text(json.dumps({"channels": {
                OLD: {**tracked_channel("Old Reactor", "2025-01-01T00:00:00Z"),
                      "dormant": True},
            }}), encoding="utf-8")
            with patch.object(reaction, "TRACKED_CHANNELS_FILE", str(path)):
                channels = reaction.load_tracked_channels()
            self.assertIn(OLD, channels)
            self.assertTrue(channels[OLD]["dormant"])

    def test_discovery_run_preserves_older_reactors_and_adds_new_ones(self):
        records = {
            OLD: tracked_channel("Old Reactor", "2025-01-01T00:00:00Z"),
            OTHER: tracked_channel("Another Reactor", "2025-03-01T00:00:00Z"),
        }
        saved = []
        with patch.object(discovery, "load_tracked_channels",
                          return_value=(copy.deepcopy(records), "2026-10-01T00:00:00Z")
        ), patch.object(discovery, "discover_channels",
                        return_value=({FRESH: "New Reaction Channel"}, {})
        ) as discover, patch.object(discovery, "save_tracked_channels",
                                   side_effect=lambda data: saved.append(copy.deepcopy(data))):
            discovery.run_discovery()
        self.assertEqual(discover.call_args.kwargs, {"include_videos": True})
        self.assertEqual(set(saved[0]), {OLD, OTHER, FRESH})
        self.assertTrue(saved[0][OLD]["dormant"])
        self.assertTrue(saved[0][OTHER]["dormant"])
        self.assertFalse(saved[0][FRESH]["dormant"])
        self.assertEqual(saved[0][OLD]["last_seen_at"], records[OLD]["last_seen_at"])

    def test_reappearance_resets_dormant_flag_without_new_registration(self):
        first_added = "2026-01-01T10:00:00Z"
        records = {OLD: {"title": "Original Name", "added_at": first_added,
                         "last_seen_at": "2025-01-01T00:00:00Z", "dormant": True}}
        saved = []
        with patch.object(discovery, "load_tracked_channels",
                          return_value=(copy.deepcopy(records), None)
        ), patch.object(discovery, "discover_channels",
                        return_value=({OLD: "Rebranded Reactor"}, {})
        ), patch.object(discovery, "save_tracked_channels",
                       side_effect=lambda data: saved.append(copy.deepcopy(data))):
            discovery.run_discovery()
        self.assertEqual(len(saved[0]), 1)
        self.assertEqual(saved[0][OLD]["added_at"], first_added)
        self.assertEqual(saved[0][OLD]["title"], "Rebranded Reactor")
        self.assertFalse(saved[0][OLD]["dormant"])
        self.assertNotEqual(saved[0][OLD]["last_seen_at"], records[OLD]["last_seen_at"])

    def test_hcjb_exclusion_still_prunes_blocked_channel(self):
        records = {
            OLD: tracked_channel("Old Reactor", "2025-01-01T00:00:00Z"),
            HCJB: tracked_channel("HCJB Music Reaction", "2025-01-01T00:00:00Z"),
        }
        saved = []
        with patch.object(discovery, "load_tracked_channels",
                          return_value=(copy.deepcopy(records), None)
        ), patch.object(discovery, "discover_channels", return_value=({}, {})
        ), patch.object(discovery, "save_tracked_channels",
                       side_effect=lambda data: saved.append(copy.deepcopy(data))):
            discovery.run_discovery()
        self.assertIn(OLD, saved[0])
        self.assertNotIn(HCJB, saved[0])

    def test_original_keyword_exclusion_still_applies(self):
        records = {
            OLD: tracked_channel("Old Reactor", "2025-01-01T00:00:00Z"),
            OTHER: tracked_channel("Unrelated vlog", "2025-01-01T00:00:00Z"),
        }
        saved = []
        with patch.object(discovery, "load_tracked_channels",
                          return_value=(copy.deepcopy(records), None)
        ), patch.object(discovery, "discover_channels", return_value=({}, {})
        ), patch.object(discovery, "save_tracked_channels",
                       side_effect=lambda data: saved.append(copy.deepcopy(data))):
            discovery.run_discovery()
        self.assertIn(OLD, saved[0])
        self.assertNotIn(OTHER, saved[0])

    def test_source_video_handoff_for_newly_discovered_channel_unchanged(self):
        fresh_stamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        saved = []
        with patch.object(discovery, "load_tracked_channels", return_value=({}, None)
        ), patch.object(discovery, "discover_channels", return_value=(
            {FRESH: "Reactions and Reviews"}, {
                FRESH: [{"video_id": "abcdefghijk", "published_at": fresh_stamp}],
            }
        )), patch.object(discovery, "save_tracked_channels",
                         side_effect=lambda data: saved.append(copy.deepcopy(data))):
            discovery.run_discovery()
        self.assertEqual(saved[0][FRESH]["discovery_videos"][0]["video_id"],
                         "abcdefghijk")

    def test_original_broad_discovery_search_unchanged(self):
        fake_youtube = MagicMock()
        fake_youtube.search.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        with patch.object(discovery, "youtube", fake_youtube):
            discovered, source = discovery.discover_channels(include_videos=True)
        self.assertEqual(discovered, {})
        self.assertEqual(source, {})
        fake_youtube.search.return_value.list.assert_called_once_with(
            part="snippet", q='"Missioned Souls"',
            type="video", maxResults=50, order="date")


if __name__ == "__main__":
    unittest.main()
