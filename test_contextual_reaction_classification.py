"""Regression tests for contextual genuine reactions without 'reaction' in title.

No real YouTube calls, Discord sends, account secrets, or notification-history edits.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/test")
import autoreactions as tracker
from new_channel_reaction_evidence import (
    clear_reaction_title, contextual_reaction_evidence,
    creator_describes_watching_subject, requires_expanded_evidence,
)
from reaction_state import load_state
from tests.test_delivery import blank_state

TERRENCE = "UCju_HuzKeD3oBO_EYLvPNmA"
VIDEO_ID = "7F7-Lma-yu4"
TITLE = "HER SOLO WAS CRAZY !! | HIGHWAY STAR - DEEP PURPLE BY MISSIONED SOULS"
DESCRIPTION = (
    "Today we are checking out this amazing Family Rock band MISSIONED SOULS "
    "playing HIGHWAY STAR BY DEEP PURPLE\n\n"
    "I hope you enjoy the video\n\nBusiness Inquiries - test@example.invalid\n"
    "Original Video - https://www.youtube.com/watch?v=F6WRxaC3QME"
)


def terrence_metadata(published=None):
    return {
        "title": TITLE,
        "description": DESCRIPTION,
        "channel": "TerrenceJr",
        "channel_id": TERRENCE,
        "video_id": VIDEO_ID,
        "published_at": published or "2026-10-23T02:48:25Z",
    }


class ContextualReactionTests(unittest.TestCase):
    def test_confirmed_terrence_reaction_is_accepted_without_reaction_keyword(self):
        item = terrence_metadata()
        self.assertTrue(creator_describes_watching_subject(item))
        self.assertTrue(contextual_reaction_evidence(item))
        self.assertTrue(clear_reaction_title(item))
        self.assertTrue(tracker.is_safe_reaction_candidate(item))
        self.assertTrue(requires_expanded_evidence(item))
        self.assertTrue(tracker.recent_enough_for_first_expanded_match(
            item, now=datetime(2026, 10, 23, 4, 30, tzinfo=timezone.utc)
        ))

    def test_other_terrence_reaction_style_without_reaction_tags(self):
        self.assertTrue(clear_reaction_title({
            "title": "I COULDNT BELIEVE IT ..... | MISSIONED SOULS - LONG TIME BY BOSTON",
            "channel": "TerrenceJr",
            "description": "Today we are checking out this amazing Family Rock band "
                           "MISSIONED SOULS playing LONG TIME BY BOSTON",
        }))

    def test_song_only_commentary_title_needs_matching_viewing_description(self):
        self.assertTrue(clear_reaction_title({
            "title": "HER SOLO WAS CRAZY !! | HIGHWAY STAR",
            "channel": "TerrenceJr",
            "description": DESCRIPTION,
        }))
        self.assertFalse(clear_reaction_title({
            "title": "HER SOLO WAS CRAZY !! | HIGHWAY STAR",
            "channel": "TerrenceJr",
            "description": "Deep Purple performed Highway Star and won the show.",
        }))

    def test_no_subjective_title_needed_with_explicit_first_person_viewing(self):
        self.assertTrue(clear_reaction_title({
            "title": "HIGHWAY STAR - DEEP PURPLE BY MISSIONED SOULS",
            "channel": "TerrenceJr",
            "description": DESCRIPTION,
        }))

    def test_neither_subject_keyword_alone_nor_loose_link_is_enough(self):
        candidates = [
            {
                "title": "Missioned Souls Highway Star",
                "channel": "Unknown",
                "description": "Don't forget to check out my channel! "
                               "Missioned Souls band cover performance.",
            },
            {
                "title": "Missioned Souls Highway Star",
                "channel": "Unknown",
                "description": "Missioned Souls is a great cover band.",
            },
            {
                "title": "Missioned Souls - Studio Cover",
                "channel": "Unknown",
                "description": "Our original performance of Missioned Souls' Highway Star. "
                               "Today we are checking out Missioned Souls.",
            },
            {
                "title": "Missioned Souls reaction archive repost",
                "channel": "TerrenceJr",
                "description": DESCRIPTION,
            },
            {
                "title": "MISSIONED SOULS - Watch Party Highlights",
                "channel": "TerrenceJr",
                "description": DESCRIPTION,
            },
            {
                "title": "Missioned Souls - Deep Purple Tribute",
                "channel": "TerrenceJr",
                "description": DESCRIPTION,
            },
            {
                "title": "Missioned Souls - Behind The Scenes",
                "channel": "TerrenceJr",
                "description": DESCRIPTION,
            },
            {
                "title": "I HATE MYSELF FOR LOVING YOU | MISSIONED SOULS | COVER",
                "channel": "The Brothers React",
                "description": "Another one! Thank you so much for requesting this one, "
                               "we just can't get enough of this family.",
            },
        ]
        for item in candidates:
            with self.subTest(title=item["title"], desc=item["description"][:30]):
                self.assertFalse(clear_reaction_title(item))

    def test_older_expanded_reaction_not_backfilled(self):
        item = terrence_metadata()
        now = datetime(2026, 10, 23, 4, 30, tzinfo=timezone.utc)
        self.assertTrue(tracker.recent_enough_for_first_expanded_match(item, now=now))
        item["published_at"] = (now - timedelta(hours=26)).isoformat()
        self.assertFalse(tracker.recent_enough_for_first_expanded_match(item, now=now))

    def test_full_tracker_accepts_terrence_not_repost_or_nonreaction_cover(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        item = terrence_metadata(recent)
        other_id = "BINBOChFeDQ"
        cover = {
            "title": "I HATE MYSELF FOR LOVING YOU | MISSIONED SOULS | COVER",
            "description": "Another one! Thank you so much for requesting this one.",
            "channel": "The Brothers React",
            "channel_id": "UCTestOtherReactor00001",
            "video_id": other_id,
            "published_at": recent,
        }
        api = MagicMock()
        api.videos.return_value.list.return_value.execute.return_value = {
            "items": [{
                "id": video["video_id"],
                "snippet": {
                    "channelId": video["channel_id"],
                    "channelTitle": video["channel"],
                    "title": video["title"],
                    "description": video["description"],
                    "publishedAt": recent,
                    "liveBroadcastContent": "none",
                    "thumbnails": {
                        "high": {"url": "https://i.ytimg.com/vi/" + video["video_id"] + "/hqdefault.jpg"}
                    },
                },
                "status": {"privacyStatus": "public"},
                "contentDetails": {"duration": "PT9M49S"},
                "statistics": {},
            } for video in (item, cover)]
        }
        def uploads(channel, channel_title):
            sample = item if channel == TERRENCE else cover
            return [{
                "title": sample["title"], "channel": sample["channel"],
                "video_id": sample["video_id"], "channel_id": sample["channel_id"],
                "published_at": recent,
                "url": "https://youtu.be/" + sample["video_id"],
                "thumbnail": None,
                "live_broadcast_content": "none", "live_streaming_details": None,
            }]

        with patch.object(tracker, "youtube", api), patch.object(
            tracker, "load_tracked_channels",
            return_value={
                TERRENCE: {"title": "TerrenceJr"},
                cover["channel_id"]: {"title": "The Brothers React"},
            }
        ), patch.object(tracker, "fetch_channel_videos", side_effect=uploads):
            accepted, run_start, scanned = tracker.get_reactions_with_stats()
        self.assertEqual(scanned, {TERRENCE, cover["channel_id"]})
        self.assertEqual([v["video_id"] for v in accepted], [VIDEO_ID])
        self.assertTrue(accepted[0]["thumbnail"])
        self.assertEqual(accepted[0]["duration_sec"], 589)
        self.assertEqual(api.videos.return_value.list.call_count, 1)

        with tempfile.TemporaryDirectory() as folder:
            bookmark = Path(folder) / "last_run.json"
            state = blank_state()
            state["known_channel_ids"] = [TERRENCE, cover["channel_id"]]
            bookmark.write_text(json.dumps(state))
            delivered = []
            def fake_send(videos, *, on_success, **kwargs):
                for video in videos:
                    delivered.append(video["video_id"])
                    on_success(video)
                return videos
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), patch.object(
                tracker, "LIVE_LAST_RUN_FILE", str(Path(folder) / "absent-live.json")
            ), patch.object(tracker, "DRY_RUN", False), patch.object(
                tracker, "send_to_discord", side_effect=fake_send
            ), patch.object(tracker, "save_durable_ids") as save_history:
                one = tracker.run_tracker(accepted, scanned, run_start=run_start)
                two = tracker.run_tracker(accepted, scanned, run_start=run_start)
            self.assertEqual([len(one), len(two)], [1, 0])
            self.assertEqual(delivered, [VIDEO_ID])
            self.assertEqual(save_history.call_count, 1)
            saved = load_state(str(bookmark))
            self.assertIn(VIDEO_ID, saved["seen_video_ids"])
            self.assertEqual(saved["pending"], [])


if __name__ == "__main__":
    unittest.main()
