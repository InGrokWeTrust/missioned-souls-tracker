"""End-to-end, OFFLINE tests for the exact video-ID discovery handoff.

No YouTube credentials, no Discord posts and no production bookmark writes.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import autoreactions as tracker
import discover_channels as discovery
from discovery_video_handoff import (
    PER_CHANNEL_LIMIT, active_candidates, add_candidates, handoff_for_scan,
)
from reaction_state import load_state
from tests.test_delivery import blank_state

NOW = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)
CHANNEL = "UCTR_PSXLloLzLMBVxnt_kLQ"
ADDED = "2026-10-22T19:01:20Z"
IDS = ["Zb7Xn%07d" % x for x in range(1, 9)]


def search_item(vid, channel=CHANNEL, title="Missioned Souls studio cover",
                pub="2026-10-22T18:10:50Z"):
    return {"id":{"kind":"youtube#video", "videoId":vid},
            "snippet":{"channelId":channel,"channelTitle":"The Mr. J Show",
                       "title":title,"publishedAt":pub}}


def registry():
    return {CHANNEL: {
        "title":"The Mr. J Show", "added_at":ADDED,
        "last_seen_at":ADDED,
        "discovery_videos":[
            {"video_id":id,
             "published_at":(NOW - timedelta(hours=1,minutes=i)).isoformat(),
             "discovered_at":ADDED}
            for i,id in enumerate(IDS)
        ]
    }}


def detail(vid, *, channel=CHANNEL, privacy="public", live="none",
           duration="PT10M", title="Missioned Souls — family cover",
           description="#reaction #reactionvideo", finished=None):
    return {"id":vid,"snippet":{
        "channelId":channel,"channelTitle":"The Mr. J Show","title":title,
        "publishedAt":"2026-10-22T18:10:50Z",
        "description":description,"liveBroadcastContent":live,
        "thumbnails":{"high":{"url":f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}},
    },"status":{"privacyStatus":privacy},
      "contentDetails":{"duration":duration},
      "statistics":{"viewCount":"1","likeCount":"2","commentCount":"0"},
      **({"liveStreamingDetails":finished} if finished is not None else {})}


class DiscoveryHandoffTests(unittest.TestCase):
    def test_discovery_saves_exact_search_video_ids_as_well_as_channel(self):
        api=MagicMock()
        api.search.return_value.list.return_value.execute.return_value={
            "items":[search_item(id) for id in IDS[:5]]
        }
        with patch.object(discovery,"youtube",api):
            channels, found = discovery.discover_channels(include_videos=True)
        self.assertEqual(channels,{CHANNEL:"The Mr. J Show"})
        self.assertEqual([x["video_id"] for x in found[CHANNEL]],IDS[:5])
        info={"title":"The Mr. J Show","added_at":ADDED}
        self.assertEqual(add_candidates(info,found[CHANNEL],now=NOW),5)
        self.assertEqual([x["video_id"] for x in info["discovery_videos"]],IDS[:5])
        self.assertEqual(len(active_candidates(info,now=NOW)),5)

    def test_bound_limit_and_expire_without_reopening_older_channels(self):
        ids=[{"video_id":f"Zb7Xn{i:07d}",
              "published_at":"2026-10-22T18:10:50Z"} for i in range(20)]
        new={"added_at":ADDED}
        self.assertEqual(add_candidates(new,ids,now=NOW),PER_CHANNEL_LIMIT)
        self.assertEqual(add_candidates(new,ids,now=NOW),0)
        self.assertEqual(len(active_candidates(new,now=NOW)),PER_CHANNEL_LIMIT)
        self.assertEqual(active_candidates(new,now=NOW+timedelta(hours=49)),[])
        old={"added_at":"2026-10-16T19:00:00Z"}
        self.assertEqual(add_candidates(old,ids,now=NOW),0)
        self.assertNotIn("discovery_videos",old)

    def test_bad_video_id_future_video_and_older_than_24h_not_handed_off(self):
        info={"added_at":ADDED}
        items=[
            {"video_id":"notvalid","published_at":"2026-10-22T18:10:50Z"},
            {"video_id":"ABCDEFGHIJK","published_at":"2026-10-20T00:00:00Z"},
            {"video_id":"ZYXWVUTSRQP","published_at":"2026-10-23T01:00:00Z"},
        ]
        self.assertEqual(add_candidates(info,items,now=NOW),0)

    def test_scanner_uses_exact_discovered_ids_outside_last_five(self):
        yt=MagicMock()
        yt.videos.return_value.list.return_value.execute.return_value={
            "items":[detail(id) for id in IDS]
        }
        with patch.object(tracker,"youtube",yt), \
             patch.object(tracker,"load_tracked_channels",return_value=registry()), \
             patch.object(tracker,"fetch_channel_videos",return_value=[]):
            found,_,scanned = tracker.get_reactions_with_stats()
        self.assertEqual(scanned,{CHANNEL})
        self.assertEqual({x["video_id"] for x in found},set(IDS))
        self.assertEqual(len(found),8)
        self.assertTrue(all(x["thumbnail"].startswith("https://i.ytimg.com/")
                            for x in found))
        self.assertEqual(yt.videos.return_value.list.call_count,1)
        kwargs=yt.videos.return_value.list.call_args.kwargs
        self.assertEqual(set(kwargs["id"].split(",")),set(IDS))
        self.assertIn("status",kwargs["part"])

    def test_all_eight_verified_discovered_reactions_deliver_six_then_two(self):
        yt=MagicMock()
        yt.videos.return_value.list.return_value.execute.return_value={
            "items":[detail(id) for id in IDS]
        }
        reg=registry()
        with patch.object(tracker,"youtube",yt), \
             patch.object(tracker,"load_tracked_channels",return_value=reg), \
             patch.object(tracker,"fetch_channel_videos",return_value=[]):
            candidates,_,scanned=tracker.get_reactions_with_stats()
        with tempfile.TemporaryDirectory() as folder:
            bookmark=Path(folder,"last_run.json")
            bookmark.write_text(json.dumps(blank_state()))
            delivered=[]
            def send(batch, **kwargs):
                for item in batch:
                    delivered.append(item["video_id"])
                    kwargs["on_success"](item)
                return batch
            with patch.object(tracker,"LAST_RUN_FILE",str(bookmark)), \
                 patch.object(tracker,"LIVE_LAST_RUN_FILE",str(Path(folder,"no-live.json"))), \
                 patch.object(tracker,"MAX_TO_SEND",6), \
                 patch.object(tracker,"send_to_discord",side_effect=send):
                kwargs={"run_start":NOW,"channel_added_at":{CHANNEL:ADDED},
                        "discovery_candidate_ids":set(IDS)}
                first=tracker.run_tracker(candidates,scanned,**kwargs)
                second=tracker.run_tracker(candidates,scanned,**kwargs)
                third=tracker.run_tracker(candidates,scanned,**kwargs)
            self.assertEqual([len(first),len(second),len(third)],[6,2,0])
            self.assertEqual(len(set(delivered)),8)
            state=load_state(bookmark)
            self.assertEqual(state["pending"],[])
            self.assertTrue(set(IDS).issubset(state["seen_video_ids"]))

    def test_verification_rejects_unrelated_private_upcoming_short_and_repost(self):
        yt=MagicMock()
        conditions=[
            detail(IDS[0],channel="wrong-uploader"),
            detail(IDS[1],privacy="private"),
            detail(IDS[2],live="upcoming"),
            detail(IDS[3],duration="PT35S"),
            detail(IDS[4],title="Missioned Souls reaction archive repost"),
            detail(IDS[5],description="music video not a reaction"),
            detail(IDS[6],live="live"),
            detail(IDS[7],title="Missioned Souls reaction",
                   finished={"actualStartTime":"2026-10-22T18:00:00Z"}),
        ]
        yt.videos.return_value.list.return_value.execute.return_value={
            "items":conditions
        }
        with patch.object(tracker,"youtube",yt), \
             patch.object(tracker,"load_tracked_channels",return_value=registry()), \
             patch.object(tracker,"fetch_channel_videos",return_value=[]):
            safe,_,_=tracker.get_reactions_with_stats()
        self.assertEqual(safe,[])

    def test_known_seen_discovery_id_is_not_posted_again(self):
        item=detail(IDS[0])
        yt=MagicMock()
        yt.videos.return_value.list.return_value.execute.return_value={"items":[item]}
        reg=registry()
        reg[CHANNEL]["discovery_videos"]=reg[CHANNEL]["discovery_videos"][:1]
        with patch.object(tracker,"youtube",yt), \
             patch.object(tracker,"load_tracked_channels",return_value=reg), \
             patch.object(tracker,"fetch_channel_videos",return_value=[]):
            safe,_,scanned=tracker.get_reactions_with_stats()
        with tempfile.TemporaryDirectory() as folder:
            bookmark=Path(folder,"last_run.json")
            state=blank_state()
            state["seen_video_ids"]=[IDS[0]]
            bookmark.write_text(json.dumps(state))
            with patch.object(tracker,"LAST_RUN_FILE",str(bookmark)), \
                 patch.object(tracker,"LIVE_LAST_RUN_FILE",str(Path(folder,"no-live.json"))), \
                 patch.object(tracker,"send_to_discord") as post:
                self.assertEqual(tracker.run_tracker(
                    safe,scanned,run_start=NOW,channel_added_at={CHANNEL:ADDED},
                    discovery_candidate_ids={IDS[0]}),[])
                post.assert_not_called()

    def test_search_hit_not_genuine_reaction_never_queued(self):
        yt=MagicMock()
        yt.videos.return_value.list.return_value.execute.return_value={
            "items":[detail(IDS[0],title="Missioned Souls studio cover",
                            description="family band studio cover")]
        }
        reg=registry()
        reg[CHANNEL]["discovery_videos"]=reg[CHANNEL]["discovery_videos"][:1]
        with patch.object(tracker,"youtube",yt), \
             patch.object(tracker,"load_tracked_channels",return_value=reg), \
             patch.object(tracker,"fetch_channel_videos",return_value=[]):
            candidates,_,_=tracker.get_reactions_with_stats()
        self.assertEqual(candidates,[])

if __name__=="__main__":
    unittest.main()
