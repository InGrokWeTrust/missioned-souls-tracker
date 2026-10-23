"""Offline checks for discovery and broadcast classification."""
import os
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/test-webhook")

import discover_channels as discovery
import check_live_streams as live
import autoreactions as tracker
from reaction_state import read_live_sent_ids, reconcile_live_notifications, StateError
from report_failure import report_failure
from types import SimpleNamespace
from pathlib import Path
import tempfile
import json


def result(i, title="Missioned Souls reaction", channel="Reaction Lab"):
    return {
        "id": {"videoId": f"id-{i}"},
        "snippet": {"title": title, "channelId": f"UC-{i}", "channelTitle": channel,
                    "publishedAt": "2026-10-21T20:00:00Z", "thumbnails": {}},
    }


class DiscoveryAndLiveTests(unittest.TestCase):
    def test_original_broad_paginated_discovery(self):
        one = {"items": [result(1)], "nextPageToken": "next-token"}
        two = {"items": [result(2)], "nextPageToken": None}
        search = MagicMock()
        search.list.return_value.execute.side_effect = [one, two]
        with patch.object(discovery, "youtube") as yt, patch.object(discovery, "DISCOVERY_PAGES", 2):
            yt.search.return_value = search
            found = discovery.discover_channels()
        self.assertEqual(set(found), {"UC-1", "UC-2"})
        self.assertEqual(search.list.call_count, 2)
        self.assertEqual(search.list.call_args_list[0].kwargs["q"], '"Missioned Souls"')
        self.assertEqual(search.list.call_args_list[1].kwargs["q"], '"Missioned Souls"')
        self.assertEqual(search.list.call_args_list[1].kwargs["pageToken"], "next-token")

    def test_unrelated_search_titles_not_added(self):
        with patch.object(discovery, "youtube") as yt:
            yt.search.return_value.list.return_value.execute.return_value = {
                "items": [result(1, title="Not about this project")]}
            self.assertEqual(discovery.discover_channels(), {})

    def test_cherman_vlogs_exception(self):
        self.assertTrue(discovery.should_track_channel("Cherman Vlogs"))
        self.assertFalse(discovery.should_track_channel("Other Vlogs"))

    def test_live_search_filters_ended_and_upcoming(self):
        search_results = {
            "items": [result(1), result(2), result(3), result(4, title="#Shorts Missioned Souls")]}
        details = {"items": [
            {"id": "id-1", "snippet": {"title": "Missioned Souls live", "liveBroadcastContent": "live"},
             "liveStreamingDetails": {"actualStartTime": "2026-10-21T20:00:00Z"}},
            {"id": "id-2", "snippet": {"title": "Missioned Souls finished", "liveBroadcastContent": "none"},
             "liveStreamingDetails": {"actualStartTime": "2026-10-21T19:00:00Z",
                                      "actualEndTime": "2026-10-21T21:00:00Z"}},
            {"id": "id-3", "snippet": {"title": "Missioned Souls scheduled", "liveBroadcastContent": "upcoming"},
             "liveStreamingDetails": {"scheduledStartTime": "2026-10-22T20:00:00Z"}},
        ]}
        with patch.object(live, "youtube") as yt:
            yt.search.return_value.list.return_value.execute.return_value = search_results
            yt.videos.return_value.list.return_value.execute.return_value = details
            streams = live.find_active_live_streams()
        self.assertEqual([v["video_id"] for v in streams], ["id-1"])
        self.assertEqual(streams[0]["published_at"], "2026-10-21T20:00:00Z")

    def test_reaction_tracker_filters_active_and_upcoming(self):
        item = {"snippet": {"title": "Missioned Souls", "channelId": "UC-1",
                            "thumbnails": {}, "publishedAt": "2026-10-22T10:00:00Z"},
                "contentDetails": {"videoId": "id-1", "videoPublishedAt": "2026-10-22T10:00:00Z"}}
        with patch.object(tracker, "load_tracked_channels", return_value={"UC-1": {"title": "Demo"}}), \
             patch.object(tracker, "youtube") as yt:
            yt.playlistItems.return_value.list.return_value.execute.return_value = {"items": [item]}
            yt.videos.return_value.list.return_value.execute.return_value = {"items": [
                {"id": "id-1", "statistics": {}, "contentDetails": {"duration": "PT3600S"},
                 "snippet": {"liveBroadcastContent": "live"},
                 "liveStreamingDetails": {"actualStartTime": "2026-10-22T10:00:00Z"}}
            ]}
            videos, _, scanned = tracker.get_reactions_with_stats()
        self.assertEqual(videos, [])
        self.assertEqual(scanned, {"UC-1"})

    def test_regex_does_not_exclude_non_short_words(self):
        item = {"title": "Missioned Souls reaction about shortstop", "duration_sec": 400}
        self.assertFalse(tracker.is_short_or_too_short(item))
        self.assertTrue(tracker.is_short_or_too_short({"title": "Missioned Souls #Shorts", "duration_sec": 150}))

    def test_cross_workflow_live_history_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "live.json"
            file.write_text(json.dumps({"sent_video_ids": ["live-1", "live-2"]}), encoding="utf-8")
            self.assertEqual(read_live_sent_ids(str(file)), {"live-1", "live-2"})

    def test_corrupt_live_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "live.json"
            file.write_text("{bad-json", encoding="utf-8")
            with self.assertRaises(StateError):
                read_live_sent_ids(str(file))

    def test_failure_alert_uses_only_stub_webhook(self):
        calls = []
        def fake_post(url, json, timeout):
            calls.append((url, json))
            return SimpleNamespace(status_code=204)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tracker-error.log"
            path.write_text("googleapiclient.errors.HttpError: quotaExceeded", encoding="utf-8")
            self.assertTrue(report_failure(
                webhook_url="https://example.invalid/alert",
                workflow="YouTube Reaction Tracker",
                run_url="https://example.invalid/run",
                log_path=str(path),
                post=fake_post
            ))
        self.assertEqual(len(calls), 1)
        text = calls[0][1]["embeds"][0]["title"]
        self.assertIn("YouTube API quota exceeded", text)
        self.assertNotIn("example.invalid/run", str(calls[0][1]))
        self.assertNotIn("Tracker workflow failed", str(calls[0][1]))

    def test_archive_duplicate_reconciled_from_live_ids(self):
        state = {"pending": [{"video_id": "id-1"}, {"video_id": "id-2"}],
                 "seen_video_ids": []}
        self.assertEqual(reconcile_live_notifications(state, {"id-1"}), 1)
        self.assertEqual(state["seen_video_ids"], ["id-1"])
        self.assertEqual([v["video_id"] for v in state["pending"]], ["id-2"])


if __name__ == "__main__":
    unittest.main()
