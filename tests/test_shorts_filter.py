"""Offline regression tests for restored pre-rollback Shorts filtering.

The original broad Missioned Souls title matching must remain unchanged.
All video cases use local dictionaries; no YouTube or Discord network traffic.
"""
import os
import unittest

os.environ.setdefault("YOUTUBE_API_KEY", "offline-shorts-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/unused")
os.environ.setdefault("HISTORY_ENABLED", "true")

import autoreactions as tracker


def candidate(title, duration=600, broadcast="none", streaming=None):
    return {
        "title": title,
        "duration_sec": duration,
        "live_broadcast_content": broadcast,
        "live_streaming_details": streaming,
    }


class RestoredShortsTests(unittest.TestCase):
    def test_hashed_short_and_shorts_labels(self):
        for label in ("#short", "#shorts", "#SHORT", "#SHORTS", "#Shorts"):
            with self.subTest(label=label):
                video = candidate("Missioned Souls reaction " + label)
                self.assertTrue(tracker.is_short_or_too_short(video))
                self.assertEqual(tracker.classify_filter_reason(video),
                                 "marked as Short")

    def test_standalone_shorts_case_insensitive(self):
        for label in ("Shorts", "SHORTS", "shorts"):
            with self.subTest(label=label):
                video = candidate("Missioned Souls " + label + " latest")
                self.assertTrue(tracker.is_short_or_too_short(video))
                self.assertEqual(tracker.classify_filter_reason(video),
                                 "marked as Short")

    def test_not_false_positive_on_unrelated_words(self):
        for title in (
            "Missioned Souls - Shortsighted Reviewer",
            "Missioned Souls - Shortsightedness in Music",
            "Missioned Souls - A shortstack of reviews",
            "Missioned Souls - #shortstop performance",
            "Missioned Souls - #shortish",
        ):
            with self.subTest(title=title):
                self.assertFalse(tracker.is_short_or_too_short(candidate(title)))

    def test_duration_rule_is_unchanged_below_120_seconds(self):
        for duration in (1, 15, 60, 119):
            with self.subTest(duration=duration):
                video = candidate("Missioned Souls - Review", duration=duration)
                self.assertTrue(tracker.is_short_or_too_short(video))
                self.assertEqual(tracker.classify_filter_reason(video),
                                 f"too short ({duration}s)")

    def test_duration_threshold_allows_120_seconds_or_more(self):
        for duration in (120, 121, 180, 600):
            with self.subTest(duration=duration):
                self.assertFalse(tracker.is_short_or_too_short(
                    candidate("Missioned Souls - NeVre reviews", duration=duration)))

    def test_unknown_duration_and_nonlabelled_livestream_exempt(self):
        self.assertFalse(tracker.is_short_or_too_short(
            candidate("Missioned Souls review", duration=0)))
        self.assertFalse(tracker.is_short_or_too_short(
            candidate("Missioned Souls live reaction", duration=30,
                      broadcast="live")))
        self.assertFalse(tracker.is_short_or_too_short(
            candidate("Missioned Souls archived stream", duration=20,
                      streaming={"actualStartTime": "2026-10-23T08:00:00Z"})))
        self.assertFalse(tracker.is_short_or_too_short(
            candidate("Missioned Souls upcoming reaction", duration=0,
                      broadcast="upcoming")))

    def test_explicit_shorts_label_still_precedes_stream_exemption(self):
        self.assertTrue(tracker.is_short_or_too_short(
            candidate("Missioned Souls #shorts", duration=400, broadcast="live")))

    def test_review_and_cover_title_matching_stays_broad(self):
        for title in (
            "Black Velvet - Alannah Myles, Missioned Souls - NeVre reviews",
            "MISSIONED SOULS Alannah Myles | BLACK VELVET COVER",
            "Missioned Souls Cover Van Halen - JUMP Reaction",
        ):
            with self.subTest(title=title):
                self.assertTrue(tracker.is_missioned_souls_reaction(title))
                self.assertFalse(tracker.is_short_or_too_short(candidate(title)))


if __name__ == "__main__":
    unittest.main()
