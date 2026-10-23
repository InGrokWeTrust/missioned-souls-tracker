"""HCJB user-requested opt-out end-to-end, without real API or Discord sends."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-only")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/no-send")

import autoreactions as reactions
import check_live_streams as live
import discover_channels as discovery
from channel_exclusions import EXCLUDED_CHANNEL_IDS, is_excluded_channel
from reaction_state import load_state

HCJB="UCtEs-cB4K1XFnXgLrIWW1Zg"
OTHER="UC8ReQVU4PPfbxRy4B18jauw"

def item(id,channel,when="2026-10-22T09:00:00Z"):
    return dict(video_id=id,channel_id=channel,title="Missioned Souls Reaction",
                published_at=when,channel="Fake Channel",
                url="https://youtu.be/"+id,
                thumbnail="https://i.ytimg.com/vi/"+id+"/mqdefault.jpg",
                live_broadcast_content="none",live_streaming_details=None,
                metadata_confirmed=True)


class ChannelExclusionTests(unittest.TestCase):
    def test_exact_id_blacklist_and_other_channels_unaffected(self):
        self.assertIn(HCJB,EXCLUDED_CHANNEL_IDS)
        self.assertTrue(is_excluded_channel(HCJB))
        self.assertFalse(is_excluded_channel(OTHER))
        self.assertFalse(is_excluded_channel(None))
        self.assertFalse(is_excluded_channel("HCJB Music Reaction"))

    def test_registry_fully_removes_hcjb_and_still_keeps_other_channels(self):
        registry=reactions.load_tracked_channels()
        self.assertGreater(len(registry), 0)  # Discovery changes registry size over time.
        self.assertNotIn(HCJB,registry)
        self.assertIn(OTHER,registry)

    def test_scanner_never_fetches_hcjb_even_if_registry_mock_includes_it(self):
        tracked={HCJB:{"title":"HCJB Music Reaction"},OTHER:{"title":"Global take"}}
        with patch.object(reactions,"load_tracked_channels",return_value=tracked), \
             patch.object(reactions,"fetch_channel_videos",return_value=[]) as fetch:
            videos,_,channels=reactions.get_reactions_with_stats()
        self.assertEqual(videos,[])
        self.assertEqual(channels,{OTHER})
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(fetch.call_args.args[0],OTHER)

    def test_old_pending_hcjb_cleared_before_delivery_but_other_preserved(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(reactions,"LAST_RUN_FILE",str(Path(d)/"last_run.json")), \
             patch.object(reactions,"LIVE_LAST_RUN_FILE",str(Path(d)/"not_exists.json")), \
             patch.object(reactions,"send_to_discord") as send, \
             patch.object(reactions,"reconfirm_older_pending_vods",return_value=set()):
            state={"version":2,"last_published_at":"2026-10-21T00:00:00Z",
                   "seen_video_ids":[],"known_channel_ids":[HCJB,OTHER],
                   "pending":[item("fromhcjb001",HCJB),item("fromglobal1",OTHER)]}
            Path(reactions.LAST_RUN_FILE).write_text(json.dumps(state))
            def confirmed(videos,**kwargs):
                cb=kwargs["on_success"]
                for v in videos:cb(v)
                return list(videos)
            send.side_effect=confirmed
            out=reactions.run_tracker([], {HCJB,OTHER})
            after=load_state(reactions.LAST_RUN_FILE)
        self.assertEqual([v["video_id"] for v in out],[])  # No fresh scan means hold pending.
        self.assertEqual([v["video_id"] for v in after["pending"]],["fromglobal1"])
        self.assertNotIn("fromhcjb001",after["seen_video_ids"])
        send.assert_not_called()

    def test_hcjb_pending_only_results_no_webhook_call(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(reactions,"LAST_RUN_FILE",str(Path(d)/"last_run.json")), \
             patch.object(reactions,"LIVE_LAST_RUN_FILE",str(Path(d)/"not_exists.json")), \
             patch.object(reactions,"send_to_discord") as send:
            state={"version":2,"last_published_at":"2026-10-21T00:00:00Z",
                   "seen_video_ids":[],"known_channel_ids":[HCJB,OTHER],
                   "pending":[item("fromhcjb001",HCJB)]}
            Path(reactions.LAST_RUN_FILE).write_text(json.dumps(state))
            out=reactions.run_tracker([], {HCJB,OTHER})
            remaining=load_state(reactions.LAST_RUN_FILE)["pending"]
        self.assertEqual(out,[])
        self.assertEqual(remaining,[])
        send.assert_not_called()

    def test_ordinary_direct_discord_sender_also_blocks_hcjb_even_on_force(self):
        candidates=[item("fromhcjb001",HCJB),item("fromglobal1",OTHER)]
        with patch.object(reactions,"post_webhook",return_value=True) as post, \
             patch.object(reactions.time,"sleep"):
            sent=reactions.send_to_discord(candidates,max_to_send=5)
        self.assertEqual([v["video_id"] for v in sent],["fromglobal1"])
        self.assertEqual(post.call_count,1)

    def test_force_replay_does_not_send_hcjb(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(reactions,"LAST_RUN_FILE",str(Path(d)/"last_run.json")), \
             patch.object(reactions,"LIVE_LAST_RUN_FILE",str(Path(d)/"not_exists.json")), \
             patch.object(reactions,"send_to_discord") as send:
            state={"version":2,"last_published_at":"2026-10-21T00:00:00Z",
                   "seen_video_ids":[],"known_channel_ids":[HCJB,OTHER],
                   "pending":[]}
            Path(reactions.LAST_RUN_FILE).write_text(json.dumps(state))
            reactions.run_tracker([item("fromhcjb001",HCJB),item("fromglobal1",OTHER)],
                                  {HCJB,OTHER},force_send_all=True)
            self.assertEqual([v["video_id"] for v in send.call_args.args[0]],["fromglobal1"])

    def test_active_live_checker_rejects_hcjb_search_and_detail_results(self):
        result={"items":[
            {"id":{"videoId":"fromhcjb001"},"snippet":{"title":"Missioned Souls LIVE",
                    "channelId":HCJB,"channelTitle":"HCJB Music Reaction","publishedAt":"2026-10-22T09:00:00Z"}},
            {"id":{"videoId":"fromglobal1"},"snippet":{"title":"Missioned Souls LIVE",
                    "channelId":OTHER,"channelTitle":"Global take","publishedAt":"2026-10-22T09:00:00Z"}}]}
        client=MagicMock()
        client.search.return_value.list.return_value.execute.return_value=result
        client.videos.return_value.list.return_value.execute.return_value={"items":[
            {"id":"fromglobal1","snippet":{"title":"Missioned Souls LIVE","channelId":OTHER,
                                         "liveBroadcastContent":"live"},
             "liveStreamingDetails":{"actualStartTime":"2026-10-22T09:00:00Z"}}]}
        with patch.object(live,"youtube",client):
            streams=live.find_active_live_streams()
        self.assertEqual([v["video_id"] for v in streams],["fromglobal1"])
        self.assertEqual(client.videos.return_value.list.call_args.kwargs["id"],"fromglobal1")

    def test_active_live_detail_also_blocks_wrong_search_channel_id(self):
        client=MagicMock()
        client.search.return_value.list.return_value.execute.return_value={"items":[
            {"id":{"videoId":"fromhcjb001"},
             "snippet":{"title":"Missioned Souls LIVE",
                        "publishedAt":"2026-10-22T09:00:00Z"}}]}
        client.videos.return_value.list.return_value.execute.return_value={"items":[
            {"id":"fromhcjb001","snippet":{"title":"Missioned Souls LIVE","channelId":HCJB,
                 "liveBroadcastContent":"live"},
             "liveStreamingDetails":{"actualStartTime":"2026-10-22T09:00:00Z"}}]}
        with patch.object(live,"youtube",client):
            self.assertEqual(live.find_active_live_streams(),[])

    def test_live_discord_sender_blocks_even_direct_hcjb_calls(self):
        streams=[item("fromhcjb001",HCJB),item("fromglobal1",OTHER)]
        with patch.object(live,"post_webhook",return_value=True) as post, \
             patch.object(live.time,"sleep"):
            sent=live.send_to_discord(streams)
        self.assertEqual([v["video_id"] for v in sent],["fromglobal1"])
        self.assertEqual(post.call_count,1)

    def test_discovery_will_not_rediscover_hcjb(self):
        client=MagicMock()
        client.search.return_value.list.return_value.execute.return_value={"items":[
            {"snippet":{"channelId":HCJB,"channelTitle":"HCJB Music Reaction",
                        "title":"Missioned Souls Rosanna"}},
            {"snippet":{"channelId":OTHER,"channelTitle":"Global take",
                        "title":"Missioned Souls Crazy Train"}}]}
        with patch.object(discovery,"youtube",client):
            observed=discovery.discover_channels()
        self.assertNotIn(HCJB,observed)
        self.assertEqual(observed[OTHER],"Global take")

if __name__=="__main__":
    unittest.main()
