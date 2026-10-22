"""Offline safety and classification regression for the independent live shadow scan."""
import unittest
from unittest.mock import patch

from scripts.keyword_live_shadow import (
    run, classify_actual, api_get, seconds_iso, fetch_uploads,
)


class RealShadowTests(unittest.TestCase):
    def test_iso_duration_parser(self):
        self.assertEqual(seconds_iso("PT1H4M12S"), 3852)
        self.assertEqual(seconds_iso("PT1M"), 60)
        self.assertEqual(seconds_iso("PT20M53S"), 1253)
        self.assertIsNone(seconds_iso("nonsense"))

    def test_real_hidden_example_positive_title_only(self):
        v={"video_id":"hf03Voz2QBM","title":"Nitro Neisha TEARS It Up On This Classic!",
           "channel":"Taylor John Hardin Reactions",
           "channel_id":"UC-lB_sG0uviaY9pD07kiwjg","url":"https://youtu.be/hf03Voz2QBM"}
        item={"snippet":{"title":v["title"],"description":"","tags":[]},
              "contentDetails":{"duration":"PT20M53S"}}
        matched=classify_actual(v,item)
        self.assertEqual(matched["tier"],"strong")
        self.assertTrue(matched["matched_without_band_title"])
        self.assertFalse(matched["would_auto_post"])

    def test_unrelated_title_negative(self):
        v={"video_id":"Lr4gUjZVjT0","title":"Nene Royal is PERFECT PITCH",
           "channel":"Taylor John Hardin Reactions", "channel_id":"UC-lB_sG0uviaY9pD07kiwjg"}
        got=classify_actual(v,{"snippet":{},"contentDetails":{"duration":"PT15M"}})
        self.assertEqual(got["tier"],"none")

    def test_short_upcoming_live_and_official_excluded(self):
        options=[
           ({"snippet":{}, "contentDetails":{"duration":"PT32S"}}, "under two minutes"),
           ({"snippet":{"liveBroadcastContent":"upcoming"}, "contentDetails":{"duration":"PT15M"}}, "live/upcoming/archived stream"),
           ({"snippet":{}, "liveStreamingDetails":{"actualStartTime":"2026-10-22T00:00:00Z"}}, "live/upcoming/archived stream"),
           ({"snippet":{}, "contentDetails":{"duration":"PT10M"}}, "official band channel (not a reactor)"),
        ]
        for i,(item,why) in enumerate(options):
            with self.subTest(reason=why):
                v={"video_id":f"abcdefgh{i:03d}", "title":"Nitro Neisha",
                   "channel":"some channel",
                   "channel_id":"UCeTfwcnUVurw7Qrk_KF5Idw" if i==3 else "UC-lB_sG0uviaY9pD07kiwjg"}
                out=classify_actual(v,item)
                self.assertEqual(out["tier"],"excluded")
                self.assertEqual(out["excluded_reason"],why)
                self.assertFalse(out["would_auto_post"])

    def test_no_key_and_redacted_api_errors(self):
        with self.assertRaises(ValueError):
            api_get("videos",{},key="")
        def fake_fail(*args,**kwargs):
            raise ValueError("private-key-token")
        with self.assertRaisesRegex(RuntimeError,"Read-only YouTube API request failed") as err:
            api_get("videos",{},key="private-key-token",getter=fake_fail)
        self.assertNotIn("private-key-token",str(err.exception))

    def test_fetch_uploads_mocked(self):
        def getter(url, params, timeout):
            self.assertIn("playlistItems",url)
            self.assertTrue(params["playlistId"].startswith("UU"))
            return type("Response",(),{
                "raise_for_status":lambda self:None,
                "json":lambda self:{"items":[{
                    "snippet":{"title":"Nitro Neisha"},
                    "contentDetails":{"videoId":"hf03Voz2QBM"}
                }]}
            })()
        v=fetch_uploads("UC-lB_sG0uviaY9pD07kiwjg","Taylor John Hardin Reactions",
                       key="fake-key",getter=getter)
        self.assertEqual(v[0]["video_id"],"hf03Voz2QBM")

    def test_live_run_with_fake_youtube_and_no_external_writes(self):
        channels={
            "UC-lB_sG0uviaY9pD07kiwjg":{"title":"Taylor John Hardin Reactions"},
            "UCRYiATJo58gU-pj82EqubFw":{"title":"WILBURN MUSIC REACTIONS"},
        }
        def fetcher(cid,channel,key):
            if "Taylor" in channel:
                return [
                    {"video_id":"hf03Voz2QBM","title":"Nitro Neisha TEARS It Up On This Classic!",
                     "channel":channel,"channel_id":cid,
                     "published_at":"2026-10-22T03:00:00Z",
                     "url":"https://youtube.com/watch?v=hf03Voz2QBM",
                     "source":"tracked uploads playlist"},
                    {"video_id":"Lr4gUjZVjT0","title":"Nene Royal reaction",
                     "channel":channel,"channel_id":cid,
                     "published_at":"2026-10-22T02:00:00Z",
                     "url":"https://youtube.com/watch?v=Lr4gUjZVjT0"}
                ]
            return [{"video_id":"kH6vzMgvfS0","title":"Missioned Souls reaction",
                     "channel":channel,"channel_id":cid,
                     "published_at":"2026-10-22T01:00:00Z",
                     "url":"https://youtube.com/watch?v=kH6vzMgvfS0"}]
        def api(endpoint,params,key):
            self.assertEqual(endpoint,"videos")
            self.assertEqual(key,"fake-key")
            return {"items":[
                {"id":"hf03Voz2QBM","snippet":{"title":"Nitro Neisha TEARS It Up On This Classic!"},
                 "contentDetails":{"duration":"PT20M53S"}},
                {"id":"Lr4gUjZVjT0","snippet":{"title":"Nene Royal reaction"},
                 "contentDetails":{"duration":"PT15M"}},
                {"id":"kH6vzMgvfS0","snippet":{"title":"Missioned Souls reaction"},
                 "contentDetails":{"duration":"PT11M"}}]}
        report=run(channels,key="fake-key",fetcher=fetcher,api=api,controls=[])
        self.assertEqual(report["channels_scanned"],2)
        self.assertEqual(report["videos_from_playlists"],3)
        self.assertEqual(report["strong"],2)
        self.assertEqual(report["none"],1)
        self.assertEqual(report["hidden_keyword_candidates"],1)
        self.assertEqual(report["candidate_examples"][0]["video_id"],"hf03Voz2QBM")
        self.assertTrue(all(r["would_auto_post"] is False for r in report["all_scanned"]))

    def test_missing_channel_is_counted_not_fabricated(self):
        channels={
            "UC-lB_sG0uviaY9pD07kiwjg":{"title":"Taylor John Hardin Reactions"},
            "UCRYiATJo58gU-pj82EqubFw":{"title":"WILBURN"},
        }
        def fetcher(cid,channel,key):
            if channel=="WILBURN":
                raise RuntimeError("temporary API failure")
            return [{"video_id":"hf03Voz2QBM","title":"Nitro Neisha",
                     "channel":channel,"channel_id":cid}]
        def api(endpoint,params,key):
            return {"items":[{"id":"hf03Voz2QBM",
                               "snippet":{"title":"Nitro Neisha"},
                               "contentDetails":{"duration":"PT15M"}}]}
        result=run(channels,key="fake-key",fetcher=fetcher,api=api,controls=[])
        self.assertEqual(result["channels_scanned"],1)
        self.assertEqual(result["channels_failed"],1)
        self.assertEqual(result["strong"],1)
        self.assertEqual(result["ground_truth"].startswith("UNREVIEWED"),True)


if __name__=="__main__":
    unittest.main()
