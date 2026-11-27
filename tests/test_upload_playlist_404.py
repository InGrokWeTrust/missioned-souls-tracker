"""Offline first-page 404 recovery; never guess an empty uploads playlist."""
import os
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")
import autoreactions as tracker
from report_failure import classify_error
from test_catchup_pagination import upload, OLD, RECENT, SCAN_START

CID = "UCFixtureTopicChannel"
EXPECTED = "UU" + CID[2:]
ACTUAL = "UUofficialAlternative"

def http(status):
    err = RuntimeError("secret-url?key=never-print")
    err.resp = SimpleNamespace(status=status)
    return err

def channel_response(cid=CID, playlist=ACTUAL):
    return {"items": [{"id": cid, "contentDetails":
                      {"relatedPlaylists": {"uploads": playlist}}}]}

class Upload404Tests(unittest.TestCase):
    def api(self, pages, metadata):
        api = MagicMock()
        api.playlistItems.return_value.list.return_value.execute.side_effect = pages
        api.channels.return_value.list.return_value.execute.return_value = metadata
        return api

    def fetch(self, api):
        with patch.object(tracker, "youtube", api), patch.object(
                tracker, "load_complete_scan", return_value=tracker._parse_utc_timestamp(SCAN_START)):
            return tracker.fetch_channel_videos(CID, "Fixture Topic")

    def test_recover_official_playlist_and_paginate(self):
        api = self.api([http(404), {"items": [upload("recent001", RECENT)],
                                   "nextPageToken": "next"},
                        {"items": [upload("old000001", OLD)]}], channel_response())
        self.assertEqual([v["video_id"] for v in self.fetch(api)],
                         ["recent001", "old000001"])
        api.channels.return_value.list.assert_called_once_with(part="contentDetails", id=CID)
        calls = api.playlistItems.return_value.list.call_args_list
        self.assertEqual([c.kwargs["playlistId"] for c in calls], [EXPECTED, ACTUAL, ACTUAL])
        self.assertNotIn("pageToken", calls[1].kwargs)
        self.assertEqual(calls[2].kwargs["pageToken"], "next")
        api.search.assert_not_called()

    def test_same_official_playlist_can_recover_transient_404(self):
        api = self.api([http(404), {"items": []}], channel_response(playlist=EXPECTED))
        self.assertEqual(self.fetch(api), [])
        self.assertEqual(api.playlistItems.return_value.list.call_count, 2)

    def test_persistent_404_fails_closed(self):
        api = self.api([http(404), http(404)], channel_response(playlist=EXPECTED))
        with self.assertRaisesRegex(RuntimeError, "Incomplete YouTube channel scan.*HTTP 404.*cutoff withheld") as exc:
            self.fetch(api)
        self.assertNotIn("never-print", str(exc.exception))
        self.assertEqual(api.playlistItems.return_value.list.call_count, 2)

    def test_missing_channel_metadata_not_empty_uploads(self):
        api = self.api([http(404)], {"items": []})
        with self.assertRaisesRegex(RuntimeError, "Incomplete YouTube channel scan.*HTTP 404"):
            self.fetch(api)
        self.assertEqual(api.playlistItems.return_value.list.call_count, 1)

    def test_mismatched_channel_metadata_not_used(self):
        api = self.api([http(404)], channel_response(cid="UCWrong"))
        with self.assertRaisesRegex(RuntimeError, "Incomplete YouTube channel scan"):
            self.fetch(api)
        self.assertEqual(api.playlistItems.return_value.list.call_count, 1)

    def test_no_fallback_for_quota_or_permissions(self):
        api = self.api([http(403)], channel_response())
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            self.fetch(api)
        api.channels.assert_not_called()

    def test_second_page_404_does_not_switch_playlist(self):
        api = self.api([{"items": [upload("recent001", RECENT)], "nextPageToken": "next"},
                        http(404)], channel_response())
        with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            self.fetch(api)
        api.channels.assert_not_called()

    def test_metadata_request_failure_is_sanitized(self):
        api = self.api([http(404)], channel_response())
        api.channels.return_value.list.return_value.execute.side_effect = http(403)
        with self.assertRaisesRegex(RuntimeError, "HTTP 403") as exc:
            self.fetch(api)
        self.assertNotIn("never-print", str(exc.exception))

    def test_specific_health_alert_for_incomplete_404_scan(self):
        result = classify_error("Traceback (most recent call last):\nRuntimeError: "
                                "Incomplete YouTube channel scan for " + CID +
                                " (HttpError, HTTP 404); cutoff withheld")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "YouTube uploads playlist unavailable")
        self.assertNotIn(CID, str(result))
        self.assertNotIn("https://", str(result))

if __name__ == "__main__":
    unittest.main()
