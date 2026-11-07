"""Offline checks for NZ livestream start timestamps, including DST.

All network and durable-history operations are mocked. No Discord delivery,
YouTube requests, or production state writes are made by this test suite.
"""
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-webhook")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as reactions
import check_live_streams as live

VIDEO_ID = "liveNZ12345"
UTC_START = "2026-11-05T15:01:00Z"


class LiveTimezoneTests(unittest.TestCase):
    def test_november_daylight_saving_is_nzdt_plus_thirteen(self):
        self.assertEqual(live.format_published_time(UTC_START),
                         "4:01 AM NZDT")
        self.assertEqual(live.format_published_time(UTC_START),
                         reactions.format_published_time(UTC_START))

    def test_july_standard_time_is_nzst_plus_twelve(self):
        value = "2026-07-04T15:01:00Z"
        self.assertEqual(live.format_published_time(value), "3:01 AM NZST")
        self.assertEqual(live.format_published_time(value),
                         reactions.format_published_time(value))

    def test_daylight_saving_fallback_disambiguates_repeated_local_hour(self):
        # On 2026-04-05 Auckland repeats 2:30 AM when daylight saving ends.
        self.assertEqual(live.format_published_time("2026-04-04T13:30:00Z"),
                         "2:30 AM NZDT")
        self.assertEqual(live.format_published_time("2026-04-04T14:30:00Z"),
                         "2:30 AM NZST")

    def test_explicit_non_utc_offset_is_converted_not_reinterpreted(self):
        self.assertEqual(
            live.format_published_time("2026-11-06T04:01:00+13:00"),
            "4:01 AM NZDT")
        self.assertEqual(
            live.format_published_time("2026-07-05T03:01:00+12:00"),
            "3:01 AM NZST")

    def test_legacy_naive_time_is_assumed_utc_like_reaction_tracker(self):
        value = "2026-11-05T15:01:00"
        self.assertEqual(live.format_published_time(value), "4:01 AM NZDT")
        self.assertEqual(live.format_published_time(value),
                         reactions.format_published_time(value))

    def test_invalid_time_falls_back_without_crashing(self):
        for value in ("invalid", "", None):
            with self.subTest(value=value):
                self.assertEqual(live.format_published_time(value), "")

    def test_discord_started_label_is_local_but_embedded_timestamp_stays_utc(self):
        stream = {
            "video_id": VIDEO_ID,
            "title": "Missioned Souls concert livestream",
            "channel_id": "UCTestLiveChannel",
            "channel": "Example Live Channel",
            "url": f"https://youtu.be/{VIDEO_ID}",
            "thumbnail": f"https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg",
            "published_at": UTC_START,
            "live_broadcast_content": "live",
        }
        run_start = datetime(2026, 11, 5, 15, 11, tzinfo=timezone.utc)
        with patch.object(live, "mark_live_discord_attempt") as before, patch.object(
            live, "clear_live_discord_attempt") as after, patch.object(
            live, "save_ids") as saved, patch.object(
            live.requests, "post", return_value=SimpleNamespace(status_code=204)
        ) as webhook, patch.object(
            live.time, "sleep") as pause, patch.object(
            live, "youtube") as youtube:
            result = live.send_to_discord([stream], run_start=run_start)
        self.assertEqual([v["video_id"] for v in result], [VIDEO_ID])
        self.assertEqual(webhook.call_count, 1)
        embed = webhook.call_args.kwargs["json"]["embeds"][0]
        self.assertEqual(embed["fields"][1]["name"], "Started")
        self.assertEqual(embed["fields"][1]["value"],
                         "4:01 AM NZDT (10m ago)")
        self.assertEqual(embed["timestamp"], UTC_START)
        self.assertEqual(embed["url"], stream["url"])
        self.assertEqual(embed["image"]["url"], stream["thumbnail"])
        before.assert_called_once_with(VIDEO_ID)
        after.assert_called_once_with(VIDEO_ID)
        saved.assert_called_once_with("live", [VIDEO_ID])
        youtube.search.assert_not_called()
        youtube.videos.assert_not_called()
        pause.assert_called_once_with(1.3)


if __name__ == "__main__":
    unittest.main()
