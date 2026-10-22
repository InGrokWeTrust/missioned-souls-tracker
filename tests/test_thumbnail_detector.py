"""Offline thumbnail OCR tests; never call YouTube, Discord, or the network."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/test")

from PIL import Image
import autoreactions as tracker
import thumbnail_detector as detector
from reaction_state import load_state, save_state

NOW = datetime(2026, 10, 22, 16, tzinfo=timezone.utc)
EXAMPLE_ID = "hf03Voz2QBM"


def sample(title="Nitro Neisha TEARS It Up On This Classic!"):
    return {
        "title": title, "video_id": EXAMPLE_ID,
        "channel": "Taylor John Hardin Reactions", "channel_id": "UC-lB_sG0uviaY9pD07kiwjg",
        "published_at": "2026-10-22T10:00:00Z",
        "url": "https://youtu.be/" + EXAMPLE_ID,
        "thumbnail": "https://i.ytimg.com/vi/" + EXAMPLE_ID + "/hqdefault.jpg"
    }


class ThumbnailTests(unittest.TestCase):
    def test_actual_example_ocr_transcript_with_misread_band_name(self):
        # Transcribed from local Tesseract against the user's actual screenshot.
        ocr_text = "NMissionevySours\\nNEISHA\\nREWRITES\\nTHE SOLO!?"
        yes, explanation = detector.text_match(ocr_text)
        self.assertTrue(yes)
        self.assertIn("approximate", explanation)

    def test_less_corrupted_band_name(self):
        self.assertTrue(detector.text_match("MissioneD}Sours\\nNEISHA")[0])
        self.assertTrue(detector.text_match("MISSIONED SOULS")[0])
        self.assertTrue(detector.text_match("MISSIONEDISOULS")[0])

    def test_generic_neisha_title_is_not_enough(self):
        self.assertFalse(detector.text_match("NEISHA REWRITES THE SOLO!?")[0])

    def test_other_artist_titles_not_matched(self):
        self.assertFalse(detector.text_match("Mission Impossible SOLOS \\nNEISHA")[0])
        self.assertFalse(detector.text_match("ROCK LEGEND MADE ME CRY!")[0])

    def test_metadata_detects_description_or_exact_tag(self):
        self.assertEqual(detector.metadata_match(sample()["title"], {
            "description": "Reacting to Missioned Souls - I Hate Myself for Loving You",
            "tags": []
        })[0], True)
        self.assertTrue(detector.metadata_match("A Reaction!", {
            "description": "", "tags": ["Missioned Souls", "neisha"]})[0])

    def test_metadata_rejects_generic_creator_biography(self):
        self.assertFalse(detector.metadata_match("Random gaming video", {
            "description": "Subscribe! I have covered many artists including Missioned Souls.",
            "tags": []})[0])

    def test_ocr_image_decoder_and_tesseract_runner_are_isolated(self):
        image = Image.new("RGB", (60, 25), "white")
        out = BytesIO()
        image.save(out, format="PNG")
        calls = []
        def executor(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=b"MISSIONED SOULS\\n", stderr=b"")
        text = detector.extract_thumbnail_text(out.getvalue(), executor=executor)
        self.assertEqual(len(calls), 2)
        self.assertIn("MISSIONED SOULS", text)

    def test_safe_thumbnail_download_mocked(self):
        def fake_get(url, timeout):
            self.assertIn("i.ytimg.com", url)
            return SimpleNamespace(
                headers={"Content-Type": "image/jpeg"}, content=b"synthetic",
                raise_for_status=lambda: None)
        hit, _ = detector.inspect_thumbnail(
            sample(), getter=fake_get, extractor=lambda _: "MissioneD}Sours NEISHA")
        self.assertTrue(hit)

    def test_reject_untrusted_thumbnail_host(self):
        video = sample()
        video["thumbnail"] = "https://example.com/redirect"
        with self.assertRaisesRegex(ValueError, "approved YouTube CDN"):
            detector.inspect_thumbnail(video)

    def test_ocr_is_disabled_by_default(self):
        with patch.object(tracker, "ENABLE_THUMBNAIL_OCR", False), \
             patch.object(tracker, "youtube") as yt:
            self.assertEqual(tracker.find_hidden_reactions(
                [sample()], {"ocr_results": {}}, NOW), [])
            yt.videos.assert_not_called()

    def test_no_title_match_is_discovered_by_thumbnail_and_reported_only(self):
        v = sample()
        with patch.object(tracker, "ENABLE_THUMBNAIL_OCR", True), \
             patch.object(tracker, "OCR_NOTIFY", False), \
             patch.object(tracker, "inspect_thumbnail", return_value=(True, "thumbnail OCR")) as inspect, \
             patch.object(tracker, "youtube") as yt:
            yt.videos.return_value.list.return_value.execute.return_value = {"items": [{
                "id": EXAMPLE_ID, "snippet": {"description": "", "tags": [],
                                              "liveBroadcastContent": "none"},
                "contentDetails": {"duration": "PT20M53S"}, "statistics": {}
            }]}
            state = {"ocr_results": {}}
            result = tracker.find_hidden_reactions([v], state, NOW)
            self.assertEqual(result, [])
            self.assertEqual(state["ocr_results"][EXAMPLE_ID]["hit"], True)
            inspect.assert_called_once()

    def test_positive_cache_can_be_sent_when_opted_in(self):
        v = sample()
        state = {"ocr_results": {
            EXAMPLE_ID: {"hit": True, "at": NOW.isoformat(),
                         "reason": "thumbnail OCR"} }}
        with patch.object(tracker, "ENABLE_THUMBNAIL_OCR", True), \
             patch.object(tracker, "OCR_NOTIFY", True), \
             patch.object(tracker, "inspect_thumbnail") as inspect, \
             patch.object(tracker, "youtube") as yt:
            yt.videos.return_value.list.return_value.execute.return_value = {"items": [{
                "id": EXAMPLE_ID, "snippet": {"description": "", "liveBroadcastContent": "none"},
                "contentDetails": {"duration": "PT20M53S"}, "statistics": {}
            }]}
            result = tracker.find_hidden_reactions([v], state, NOW)
            self.assertEqual([x["video_id"] for x in result], [EXAMPLE_ID])
            inspect.assert_not_called()

    def test_negative_ocr_cache_skips_repeat_fetch_within_one_day(self):
        v = sample()
        state = {"ocr_results": {
            EXAMPLE_ID: {"hit": False, "at": NOW.isoformat(), "reason": "no match"}}}
        with patch.object(tracker, "ENABLE_THUMBNAIL_OCR", True), \
             patch.object(tracker, "inspect_thumbnail") as inspect, \
             patch.object(tracker, "youtube") as yt:
            yt.videos.return_value.list.return_value.execute.return_value = {"items": [{
                "id": EXAMPLE_ID, "snippet": {"description": "", "liveBroadcastContent": "none"},
                "contentDetails": {"duration": "PT20M53S"}, "statistics": {}
            }]}
            self.assertEqual(tracker.find_hidden_reactions([v], state, NOW), [])
            inspect.assert_not_called()

    def test_ocr_failure_is_not_cached(self):
        v = sample()
        state = {"ocr_results": {}}
        with patch.object(tracker, "ENABLE_THUMBNAIL_OCR", True), \
             patch.object(tracker, "inspect_thumbnail", side_effect=RuntimeError("temporary")) as inspect, \
             patch.object(tracker, "youtube") as yt:
            yt.videos.return_value.list.return_value.execute.return_value = {"items": [{
                "id": EXAMPLE_ID, "snippet": {"description": "", "liveBroadcastContent": "none"},
                "contentDetails": {"duration": "PT20M53S"}, "statistics": {}
            }]}
            self.assertEqual(tracker.find_hidden_reactions([v], state, NOW), [])
        self.assertEqual(state["ocr_results"], {})

    def test_ocr_ignored_for_short_and_live(self):
        for broadcast, duration in [("live", "PT20M"), ("upcoming", "PT0S"),
                                    ("none", "PT59S")]:
            with self.subTest(broadcast=broadcast, duration=duration):
                with patch.object(tracker, "ENABLE_THUMBNAIL_OCR", True), \
                     patch.object(tracker, "inspect_thumbnail") as inspect, \
                     patch.object(tracker, "youtube") as yt:
                    yt.videos.return_value.list.return_value.execute.return_value = {"items": [{
                        "id": EXAMPLE_ID,
                        "snippet": {"description": "", "liveBroadcastContent": broadcast},
                        "contentDetails": {"duration": duration}, "statistics": {}
                    }]}
                    self.assertEqual(tracker.find_hidden_reactions([sample()], {"ocr_results": {}}, NOW), [])
                    inspect.assert_not_called()

    def test_end_to_end_playlist_scan_without_title_keyword(self):
        video = sample()
        item = {
            "snippet": {
                "title": video["title"],
                "thumbnails": {"high": {"url": video["thumbnail"]}},
                "publishedAt": video["published_at"],
            },
            "contentDetails": {
                "videoId": EXAMPLE_ID, "videoPublishedAt": video["published_at"]
            }
        }
        details = {
            "id": EXAMPLE_ID,
            "snippet": {
                "description": "", "tags": [], "liveBroadcastContent": "none"
            },
            "statistics": {"viewCount": "1000"},
            "contentDetails": {"duration": "PT20M53S"}
        }
        with patch.object(tracker, "load_tracked_channels", return_value={
                video["channel_id"]: {"title": video["channel"]}
             }), patch.object(tracker, "ENABLE_THUMBNAIL_OCR", True), \
             patch.object(tracker, "OCR_NOTIFY", True), \
             patch.object(tracker, "inspect_thumbnail",
                          return_value=(True, "approximate band plus Neisha")) as inspect, \
             patch.object(tracker, "youtube") as yt:
            yt.playlistItems.return_value.list.return_value.execute.return_value = {"items": [item]}
            yt.videos.return_value.list.return_value.execute.return_value = {"items": [details]}
            videos, _, scanned = tracker.get_reactions_with_stats(
                state={"ocr_results": {}})
        self.assertEqual([v["video_id"] for v in videos], [EXAMPLE_ID])
        self.assertEqual(scanned, {video["channel_id"]})
        inspect.assert_called_once()

    def test_skip_already_notified_thumbnail(self):
        with patch.object(tracker, "ENABLE_THUMBNAIL_OCR", True), \
             patch.object(tracker, "youtube") as yt:
            found = tracker.find_hidden_reactions(
                [sample()], {"ocr_results": {}, "seen_video_ids": [EXAMPLE_ID],
                             "pending": []}, NOW)
        self.assertEqual(found, [])
        yt.videos.assert_not_called()

    def test_throttle_ocr_to_configured_budget(self):
        videos = []
        details = []
        for i in range(5):
            vid = sample()
            vid["video_id"] = f"abcdefgh0{i}"
            vid["thumbnail"] = f"https://i.ytimg.com/vi/{vid['video_id']}/hqdefault.jpg"
            videos.append(vid)
            details.append({
                "id": vid["video_id"],
                "snippet": {"description": "", "liveBroadcastContent": "none"},
                "contentDetails": {"duration": "PT15M"},
                "statistics": {}
            })
        with patch.object(tracker, "ENABLE_THUMBNAIL_OCR", True), \
             patch.object(tracker, "MAX_OCR_PER_RUN", 2), \
             patch.object(tracker, "inspect_thumbnail", return_value=(False, "no match")) as inspect, \
             patch.object(tracker, "youtube") as yt:
            yt.videos.return_value.list.return_value.execute.return_value = {"items": details}
            found = tracker.find_hidden_reactions(videos, {"ocr_results": {}}, NOW)
        self.assertEqual(found, [])
        self.assertEqual(inspect.call_count, 2)

    def test_state_roundtrip_preserves_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bookmark.json")
            s = {"version": 2, "last_published_at": "2026-10-22T10:00:00Z",
                 "seen_video_ids": [], "known_channel_ids": [], "pending": [],
                 "ocr_results": {EXAMPLE_ID: {"hit": True, "at": NOW.isoformat(),
                                              "reason": "OCR"}}}
            save_state(p, s)
            self.assertEqual(load_state(p)["ocr_results"][EXAMPLE_ID]["hit"], True)


if __name__ == "__main__":
    unittest.main()
