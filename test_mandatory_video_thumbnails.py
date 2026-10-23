"""Offline checks: video Discord embeds must ALWAYS contain a YouTube thumbnail."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from thumbnail_policy import (
    MissingVideoThumbnail, valid_thumbnail, select_thumbnail, require_thumbnails,
)
import autoreactions as reactions
import check_live_streams as live
from reaction_state import load_state
from tests.test_delivery import NOW, blank_state, video


class MandatoryThumbnailTests(unittest.TestCase):
    def test_valid_thumbnail_must_match_same_youtube_video(self):
        self.assertTrue(valid_thumbnail(
            "https://i.ytimg.com/vi/TZ7z0pZvn8w/mqdefault.jpg", "TZ7z0pZvn8w"))
        self.assertTrue(valid_thumbnail(
            "https://i.ytimg.com/vi_webp/TZ7z0pZvn8w/mqdefault.webp", "TZ7z0pZvn8w"))
        for invalid in (
            None, "", "http://i.ytimg.com/vi/TZ7z0pZvn8w/mqdefault.jpg",
            "https://evil.example.com/vi/TZ7z0pZvn8w/mqdefault.jpg",
            "https://i.ytimg.com/vi/otherID/mqdefault.jpg",
            "https://i.ytimg.com/vi/TZ7z0pZvn8w/",
            "https://i.ytimg.com/vi/TZ7z0pZvn8w/mqdefault.jpg#fragment",
            "https://user:pass@i.ytimg.com/vi/TZ7z0pZvn8w/mqdefault.jpg",
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(valid_thumbnail(invalid, "TZ7z0pZvn8w"))

    def test_thumbnail_metadata_uses_safe_quality_fallback(self):
        data = {
            "medium": {"url": "http://broken.example.com/image.jpg"},
            "high": {"url": "https://i.ytimg.com/vi/id-1/hqdefault.jpg"},
            "default": {"url": "https://i.ytimg.com/vi/id-1/default.jpg"},
        }
        self.assertEqual(select_thumbnail(data, "id-1"), data["high"]["url"])
        self.assertEqual(
            select_thumbnail({}, "id-1", fallback=data["default"]["url"]),
            data["default"]["url"],
        )
        self.assertIsNone(select_thumbnail({}, "id-1"))
        with self.assertRaisesRegex(MissingVideoThumbnail, "no Discord text-only"):
            require_thumbnails([{"video_id":"id-1","thumbnail":None}])

    def test_reaction_sender_never_posts_any_item_in_bad_batch(self):
        first = video(1)
        second = video(2)
        second.pop("thumbnail")
        callbacks = []
        with patch.object(reactions, "post_webhook") as post:
            with self.assertRaisesRegex(MissingVideoThumbnail, "id-2"):
                reactions.send_to_discord(
                    [first, second],
                    on_success=lambda item: callbacks.append(item["video_id"]),
                )
            post.assert_not_called()
        self.assertEqual(callbacks, [])

    def test_reaction_sender_has_image_and_original_fields(self):
        item = video(1)
        with patch.object(reactions, "post_webhook", return_value=True) as post, \
             patch.object(reactions.time, "sleep"):
            delivered = reactions.send_to_discord([item])
        self.assertEqual(delivered, [item])
        embed = post.call_args.args[1]["embeds"][0]
        self.assertEqual(embed["image"], {"url":item["thumbnail"]})
        self.assertEqual(
            [f["name"] for f in embed["fields"]],
            ["Channel", "Views", "Posted", "Likes", "Comment"],
        )

    def test_missing_reaction_thumbnail_keeps_pending_and_not_seen(self):
        with tempfile.TemporaryDirectory() as d:
            bookmark = Path(d, "last_run.json")
            bookmark.write_text(json.dumps(blank_state()), encoding="utf-8")
            item = video(1)
            item.pop("thumbnail")
            with patch.object(reactions, "LAST_RUN_FILE", str(bookmark)), \
                 patch.object(reactions, "LIVE_LAST_RUN_FILE", str(Path(d, "missing-live.json"))), \
                 patch.object(reactions, "post_webhook") as post:
                with self.assertRaises(MissingVideoThumbnail):
                    reactions.run_tracker([item], {"UC1"}, run_start=NOW)
                post.assert_not_called()
            state = load_state(bookmark)
            self.assertEqual([v["video_id"] for v in state["pending"]], ["id-1"])
            self.assertNotIn("id-1", state["seen_video_ids"])

    def test_pending_reaction_delivered_when_thumbnail_returns_next_scan(self):
        with tempfile.TemporaryDirectory() as d:
            bookmark = Path(d, "last_run.json")
            bookmark.write_text(json.dumps(blank_state()), encoding="utf-8")
            incomplete = video(1)
            incomplete.pop("thumbnail")
            with patch.object(reactions, "LAST_RUN_FILE", str(bookmark)), \
                 patch.object(reactions, "LIVE_LAST_RUN_FILE", str(Path(d, "missing-live.json"))), \
                 patch.object(reactions, "post_webhook", return_value=True) as post, \
                 patch.object(reactions.time, "sleep"):
                with self.assertRaises(MissingVideoThumbnail):
                    reactions.run_tracker([incomplete], {"UC1"}, run_start=NOW)
                sent = reactions.run_tracker([video(1)], {"UC1"}, run_start=NOW)
                repeat = reactions.run_tracker([video(1)], {"UC1"}, run_start=NOW)
            self.assertEqual([v["video_id"] for v in sent], ["id-1"])
            self.assertEqual(repeat, [])
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.args[1]["embeds"][0]["image"],
                             {"url":video(1)["thumbnail"]})
            self.assertEqual(load_state(bookmark)["pending"], [])

    def test_livestream_sender_never_posts_text_only(self):
        one = video(1)
        one.pop("thumbnail")
        with patch.object(live, "post_webhook") as post:
            with self.assertRaises(MissingVideoThumbnail):
                live.send_to_discord([one])
            post.assert_not_called()

    def test_livestream_sender_preserves_full_size_image(self):
        item = video(1)
        with patch.object(live, "post_webhook", return_value=True) as post, \
             patch.object(live.time, "sleep"):
            sent = live.send_to_discord([item])
        self.assertEqual(sent, [item])
        embed = post.call_args.args[1]["embeds"][0]
        self.assertEqual(embed["image"], {"url":item["thumbnail"]})
        self.assertEqual(embed["color"], 0xe53935)
        self.assertEqual([f["name"] for f in embed["fields"]], ["Channel","Started"])

    def test_old_pending_reconfirm_uses_metadata_thumbnail(self):
        previous = video(1)
        previous.pop("thumbnail")
        yt = MagicMock()
        yt.videos.return_value.list.return_value.execute.return_value = {
            "items":[{
                "id":"id-1",
                "snippet":{
                    "channelId":"UC1",
                    "title":previous["title"],
                    "description":"#reaction #reactionvideo",
                    "liveBroadcastContent":"none",
                    "thumbnails":{
                        "medium":{"url":"https://i.ytimg.com/vi/id-1/mqdefault.jpg"}
                    },
                },
                "contentDetails":{"duration":"PT7M"},
                "status":{"privacyStatus":"public"},
            }],
        }
        with patch.object(reactions, "youtube", yt):
            ready = reactions.reconfirm_older_pending_vods([previous], set(), now=NOW)
        self.assertEqual(ready, {"id-1"})
        self.assertEqual(previous["thumbnail"],
                         "https://i.ytimg.com/vi/id-1/mqdefault.jpg")

    def test_discovery_scanner_can_recover_missing_playlist_thumbnail(self):
        yt = MagicMock()
        yt.videos.return_value.list.return_value.execute.return_value = {
            "items":[{
                "id":"id-1",
                "snippet":{
                    "description":"",
                    "liveBroadcastContent":"none",
                    "thumbnails":{
                        "high":{"url":"https://i.ytimg.com/vi/id-1/hqdefault.jpg"}
                    },
                },
                "contentDetails":{"duration":"PT8M"},
                "statistics":{"viewCount":"20"},
            }]
        }
        item=video(1)
        item.pop("thumbnail")
        with patch.object(reactions, "youtube", yt), \
             patch.object(reactions, "load_tracked_channels",
                          return_value={"UC1":{"title":"Demo"}}), \
             patch.object(reactions, "fetch_channel_videos", return_value=[item]):
            candidates,_,_=reactions.get_reactions_with_stats()
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]["thumbnail"],
                         "https://i.ytimg.com/vi/id-1/hqdefault.jpg")
        self.assertEqual(yt.videos.return_value.list.call_count, 1)


if __name__ == "__main__":
    unittest.main()
