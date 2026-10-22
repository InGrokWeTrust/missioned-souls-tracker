"""Zero-network regression tests for the sole user-verified Nitro Neisha VOD."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-only")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/offline-test")

import autoreactions as tracker
from verified_nitro_reactions import (
    is_verified_nitro_reaction, VERIFIED_NITRO_VIDEOS,
)
from channel_exclusions import EXCLUDED_CHANNEL_IDS
from reaction_state import plan_notifications, load_state

VID="hf03Voz2QBM"
CID="UC-lB_sG0uviaY9pD07kiwjg"
TITLE="Nitro Neisha TEARS It Up On This Classic!"


class VerifiedNitroSafetyTests(unittest.TestCase):
    def test_human_confirmed_video_and_channel_exactly_whitelisted(self):
        self.assertEqual(VERIFIED_NITRO_VIDEOS, {VID:CID})
        self.assertTrue(is_verified_nitro_reaction(dict(video_id=VID,channel_id=CID,title=TITLE)))
        self.assertTrue(tracker.is_missioned_souls_reaction(TITLE,video_id=VID,channel_id=CID))

    def test_nitro_title_without_verified_video_id_never_matches(self):
        title="Nitro Neisha - a reaction by another fan"
        self.assertFalse(tracker.is_missioned_souls_reaction(
            title,video_id="unknownvid1",channel_id=CID))

    def test_known_id_from_wrong_uploading_channel_rejected(self):
        self.assertFalse(tracker.is_missioned_souls_reaction(
            TITLE,video_id=VID,channel_id="UC8ReQVU4PPfbxRy4B18jauw"))

    def test_mismatched_title_metadata_not_assumed_valid(self):
        self.assertFalse(tracker.is_missioned_souls_reaction(
            "Paper Dove - music",video_id=VID,channel_id=CID))

    def test_hcjb_exclusion_remains_in_force(self):
        self.assertIn("UCtEs-cB4K1XFnXgLrIWW1Zg", EXCLUDED_CHANNEL_IDS)
        self.assertNotIn(CID, EXCLUDED_CHANNEL_IDS)

    def test_old_band_title_rule_unchanged(self):
        self.assertTrue(tracker.is_missioned_souls_reaction("Missioned Souls - Rosanna reaction"))
        self.assertTrue(tracker.is_missioned_souls_reaction("missioned souls WHAT A COVER"))
        self.assertFalse(tracker.is_missioned_souls_reaction("Unknown guitarist and singer"))

    def test_empty_ids_do_not_trigger_exception(self):
        self.assertFalse(tracker.is_missioned_souls_reaction(TITLE))
        self.assertFalse(tracker.is_missioned_souls_reaction(TITLE, video_id=VID))
        self.assertFalse(is_verified_nitro_reaction(None))

    def test_normal_scanner_accepts_verified_nitro_from_tracked_uploader(self):
        item={
            "title":TITLE,"video_id":VID,
            "channel_id":CID,"channel":"Taylor John Hardin Reactions",
            "published_at":"2026-10-22T05:00:00Z",
            "url":"https://youtu.be/"+VID,
            "thumbnail":None,"live_broadcast_content":"none",
            "live_streaming_details":None,
        }
        matched={
            "id":VID,"statistics":{},
            "contentDetails":{"duration":"PT19M"},
            "snippet":{"liveBroadcastContent":"none"},
        }
        api=MagicMock()
        api.videos.return_value.list.return_value.execute.return_value={"items":[matched]}
        with patch.object(tracker,"load_tracked_channels",return_value={CID:{"title":"Taylor John Hardin Reactions"}}), \
             patch.object(tracker,"fetch_channel_videos",return_value=[item]), \
             patch.object(tracker,"youtube",api):
            result,_,ids=tracker.get_reactions_with_stats()
        self.assertEqual([v["video_id"] for v in result],[VID])
        self.assertEqual(ids,{CID})
        self.assertEqual(api.videos.return_value.list.call_count,1)

    def test_only_latest_five_uploads_scanned_not_entire_channel_archive(self):
        self.assertEqual(tracker.MAX_VIDEOS_PER_CHANNEL,5)
        # Older videos beyond the most recent five cannot be discovered by
        # manually dispatching the ordinary workflow.
        with patch.object(tracker,"load_tracked_channels",return_value={CID:{"title":"Taylor John Hardin Reactions"}}), \
             patch.object(tracker,"fetch_channel_videos",return_value=[]):
            result,_,ids=tracker.get_reactions_with_stats()
        self.assertEqual(result,[])
        self.assertEqual(ids,{CID})

    def test_seven_day_lookup_does_not_enqueue_old_unseen_video(self):
        state={
            "version":2,"last_published_at":"2026-10-21T00:00:00Z",
            "seen_video_ids":[],"known_channel_ids":[CID],"pending":[]
        }
        item={"video_id":VID,"channel_id":CID,"title":TITLE,
              "published_at":"2026-09-01T00:00:00Z"}
        plan_notifications(state,[item],{CID},now=datetime(2026,10,22,20,tzinfo=timezone.utc))
        self.assertEqual(state["pending"],[])

    def test_verified_nitro_not_resent_when_already_seen_or_queued(self):
        state={
            "version":2,"last_published_at":"2026-10-20T00:00:00Z",
            "seen_video_ids":[VID],"known_channel_ids":[CID],"pending":[]
        }
        item={"video_id":VID,"channel_id":CID,"title":TITLE,
              "published_at":"2026-10-22T05:00:00Z"}
        plan_notifications(state,[item],{CID},now=datetime(2026,10,22,20,tzinfo=timezone.utc))
        self.assertEqual(state["pending"],[])
        state["seen_video_ids"]=[]
        state["pending"]=[item]
        plan_notifications(state,[item],{CID},now=datetime(2026,10,22,20,tzinfo=timezone.utc))
        self.assertEqual(len(state["pending"]),1)


if __name__=="__main__":
    unittest.main()
