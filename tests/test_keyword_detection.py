"""Offline regression of all zero-fee keyword evidence rules.

Real titles are sourced from the user's supplied video screenshot and
previous public reactor-video checks; constructed edge cases are synthetic.
No YouTube API requests or Discord sends are made.
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY","test-only")
os.environ.setdefault("DISCORD_WEBHOOK_URL","https://example.invalid/not-sent")
import autoreactions as tracker
from keyword_detector import classify, report_unmatched


class KeywordClassifierTests(unittest.TestCase):
    def tier(self, title, description="", tags=None, *, expected, strong=False):
        got=classify(title,description,tags)
        self.assertEqual(got["tier"],expected,got)
        self.assertEqual(got["auto_candidate"],strong,got)
        return got

    # Actual identified videos, not fabricated titles.
    def test_real_nitro_neisha_missing_band_name(self):
        out=self.tier("Nitro Neisha TEARS It Up On This Classic!",
                      expected="strong",strong=True)
        self.assertIn("Nitro Neisha", " ".join(out["evidence"]))

    def test_real_four_members_hidden_band_name(self):
        self.tier('🎸 They Made "Crazy Train" Sound Incredible! | Naces, Neisha, Stacey & Isaiah | Faceless Reaction',
                  "Naces (Bass), Neisha (Lead Guitar), Stacey (Vocals), Isaiah (Drums)",
                  expected="review")

    def test_real_wilburn_explicit_title(self):
        self.tier("THIS 11 YEAR OLD DRUMMER IS UNREAL - MISSIONED SOULS",
                  expected="strong",strong=True)

    def test_real_stacey_vocal_focus_explicit_title(self):
        self.tier("Musician Reacts to Missioned Souls – Sweet Child O' Mine | Stacey Vocal Focus",
                  expected="strong",strong=True)

    def test_real_unrelated_taylor_nene(self):
        self.tier("Nene Royal is PERFECT PITCH | Set Fire to the Rain",expected="none")

    def test_real_unrelated_taylor_dany(self):
        self.tier("The Warning's Dany Villarreal Guitar Interview",expected="none")

    def test_real_unrelated_taylor_one_direction(self):
        self.tier("Guitarist Reacts to One Direction",expected="none")

    def test_nitro_spelling_variants(self):
        for t in ["NITRO NEISHA is ridiculous!", "Nitro Neeisha – solo", "nitro-neisha guitar"]:
            with self.subTest(t=t):
                self.tier(t,expected="strong",strong=True)

    def test_artist_spacing_and_case(self):
        for t in ["Missioned Souls reacts", "#MissionedSouls epic cover",
                  "MISSIONED-SOULS studio reaction", "missioned_souls"]:
            with self.subTest(t=t):
                self.tier(t,expected="strong",strong=True)

    def test_unverified_mission_souls_spelling(self):
        self.tier("Mission Souls This Family Band Rocks", expected="review")

    def test_neisha_guitar_solo(self):
        self.tier("Neisha SHREDS THIS GUITAR SOLO",expected="review")

    def test_neesha_alternate_spelling(self):
        self.tier("Neesha guitar solo reaction",expected="review")

    def test_stacey_stacy_vocals(self):
        for t in ["Stacey lead vocals blew me away", "Stacy the singer reacts"]:
            with self.subTest(t=t):
                self.tier(t,expected="review")

    def test_ice_drumming(self):
        self.tier("Ice barefoot drummer is unbelievable",expected="review")

    def test_isiah_isaiah_drums(self):
        for t in ["Isiah drums reaction", "Isaiah on the drums"]:
            with self.subTest(t=t):
                self.tier(t,expected="review")

    def test_naces_bass_base_keyboard(self):
        for t in ["Naces bass sounds incredible", "Naces on base guitar",
                  "Nace's keyboards blow me away", "NACES KEYS"]:
            with self.subTest(t=t):
                self.tier(t,expected="review")

    def test_sheena_or_secan_role_only_review(self):
        self.tier("Sheena lead vocals",expected="review")
        self.tier("Secan mixing engineer",expected="review")

    def test_multiple_member_names_without_instruments_not_automatic(self):
        self.tier("Neisha Stacey Naces on this great song",expected="none")

    def test_neisha_only_is_not_enough(self):
        self.tier("Neisha is amazing",expected="none")

    def test_stacey_only_is_not_enough(self):
        self.tier("Stacey wins the show",expected="none")

    def test_naces_only_is_not_enough(self):
        self.tier("Naces is amazing",expected="none")

    def test_ice_only_is_not_enough(self):
        self.tier("Ice, the star",expected="none")

    def test_generic_drumming_is_not_enough(self):
        self.tier("This barefoot drummer is insane",expected="none")

    def test_other_ice_not_mistaken_for_member(self):
        for t in ["Ice hockey drummer", "Ice cream drum kit", "Stacey Solomon lead vocals"]:
            with self.subTest(t=t):
                got=classify(t)
                self.assertFalse(got["auto_candidate"])

    def test_song_title_alone_not_evidence(self):
        for t in ["I Hate Myself for Loving You Reaction",
                  "Crazy Train - guitar cover", "Sweet Child O' Mine", "Long Time Reaction"]:
            with self.subTest(t=t):
                self.tier(t,expected="none")

    def test_full_band_name_in_description_requires_review(self):
        out=self.tier("Amazing Guitar Reaction", "Reacting to Missioned Souls version of Crazy Train",
                      expected="review")
        self.assertIn("description", " ".join(out["evidence"]))

    def test_description_boilerplate_not_automatic(self):
        self.tier("Unrelated Music Reaction",
                  "Past reactions: Beyonce, Metallica, Missioned Souls, Nene Royal",
                  expected="review")

    def test_description_context_member_roles_only_review(self):
        self.tier("These Kids Are Phenomenal Reaction",
                  "Neisha's guitar solo stunned me; Stacey sings lead vocals", expected="review")

    def test_exact_band_tag_without_title_evidence_review(self):
        self.tier("Wow, I didn't expect this REACTION",tags=["music","MissionedSouls"],
                  expected="review")

    def test_live_false_positive_crime_titles_with_only_band_tags(self):
        for title in [
            "POLICE - I DIDN'T DO ANYTHING - TOO FUNNY AND ANNOYING",
            'Crime Time Friday EP-2 - "DANCER" Refuses to Exit Her Car, Gets Dragged Out',
            "Crime Time Friday EP-1 Honda CBR500 130MPH - Teen charged with FELONY",
        ]:
            with self.subTest(title=title):
                out=self.tier(title,tags=["Missioned Souls","reaction","music"],
                              expected="none")
                self.assertIn("isolated band tag", " ".join(out["reasons"]))

    def test_unrelated_stacey_solomon_vocals_not_member_evidence(self):
        self.tier("Stacey Solomon vocal lessons",expected="none")

    def test_generic_tags_do_not_influence(self):
        self.tier("One Direction reaction",tags=["guitar", "music", "rock"],expected="none")

    def test_untracked_channels_not_classified(self):
        got=classify("Nitro Neisha",tracked_reactor=False)
        self.assertEqual(got["tier"],"none")

    def test_empty_unicode_and_word_boundaries(self):
        self.tier("",expected="none")
        self.tier("missionedsoulsfake",expected="none")
        self.tier("Nacesss bass",expected="none")
        self.tier("Ｍｉｓｓｉｏｎｅｄ Ｓｏｕｌｓ",expected="strong",strong=True)

    def test_description_match_outside_650_chars_ignored(self):
        self.tier("A random upload","."*700 + " Missioned Souls reaction",
                  expected="none")

    def test_report_unmatched_realistic_batched_youtube_metadata(self):
        vids=[{"video_id":"hf03Voz2QBM",
               "title":"Nitro Neisha TEARS It Up On This Classic!",
               "channel":"Taylor John Hardin Reactions",
               "published_at":"2026-10-22T03:00:00Z"},
              {"video_id":"kH6vzMgvfS0",
               "title":"Missioned Souls 11 Year Old Drummer Is Unreal",
               "channel":"WILBURN MUSIC REACTIONS",
               "published_at":"2026-10-22T03:00:00Z"},
              {"video_id":"Lr4gUjZVjT0","title":"Nene Royal Reacts",
               "channel":"Taylor John Hardin Reactions",
               "published_at":"2026-10-22T03:00:00Z"}]
        class FakeVideos:
            def list(self, part, id):
                self.request=(part,id)
                return self
            def execute(self):
                return {"items":[
                    {"id":"hf03Voz2QBM","snippet":{"description":"", "tags":[]}},
                    {"id":"kH6vzMgvfS0","snippet":{"description":"", "tags":[]}},
                    {"id":"Lr4gUjZVjT0","snippet":{"description":"", "tags":[]}}
                ]}
        class FakeClient:
            v=FakeVideos()
            def videos(self):return self.v
        yt=FakeClient()
        result=report_unmatched(vids,yt)
        self.assertEqual(result["examined"],3)
        self.assertEqual(len(result["strong"]),2)
        self.assertEqual(result["none"],1)
        self.assertEqual(yt.v.request[0],"snippet,contentDetails,liveStreamingDetails")

    def test_report_skips_shorts_and_upcoming_and_archived_lives(self):
        items=[
            {"video_id":"abcdefgh001","title":"Nitro Neisha #shorts",
             "channel":"Tracked reactor","published_at":"2026-10-22T03:00:00Z"},
            {"video_id":"abcdefgh002","title":"Nitro Neisha",
             "channel":"Tracked reactor","published_at":"2026-10-22T03:00:00Z"},
            {"video_id":"abcdefgh003","title":"Nitro Neisha",
             "channel":"Tracked reactor","published_at":"2026-10-22T03:00:00Z"},
            {"video_id":"abcdefgh004","title":"Nitro Neisha",
             "channel":"Tracked reactor","published_at":"2026-10-22T03:00:00Z"},
        ]
        class FakeRequest:
            def execute(self):
                return {"items":[
                    {"id":"abcdefgh001","snippet":{},"contentDetails":{"duration":"PT20S"}},
                    {"id":"abcdefgh002","snippet":{"liveBroadcastContent":"upcoming"}},
                    {"id":"abcdefgh003","snippet":{},"liveStreamingDetails":{"actualStartTime":"2026-10-22T01:00:00Z"}},
                    {"id":"abcdefgh004","snippet":{},"contentDetails":{"duration":"PT10M"}},
                ]}
        class FakeClient:
            def videos(self):return self
            def list(self,**kwargs):return FakeRequest()
        result=report_unmatched(items,FakeClient())
        self.assertEqual(result["excluded_shorts_or_live"],3)
        self.assertEqual(len(result["strong"]),1)

    def test_report_only_does_not_add_candidate_to_discord_queue(self):
        """Simulate one channel with a Nitro title, ensure returned video list remains empty."""
        playlist={
            "items":[{"snippet":{"title":"Nitro Neisha TEARS It Up On This Classic!",
                       "publishedAt":"2026-10-22T03:00:00Z"},
                       "contentDetails":{"videoId":"hf03Voz2QBM",
                                         "videoPublishedAt":"2026-10-22T03:00:00Z"}}]
        }
        class Request:
            def __init__(self,payload):self.payload=payload
            def execute(self):return self.payload
        class Client:
            def playlistItems(self):return self
            def videos(self):return self
            def list(self,**kw):
                return Request(playlist if "playlistId" in kw else
                               {"items":[{"id":"hf03Voz2QBM","snippet":{
                                   "title":"Nitro Neisha TEARS It Up On This Classic!",
                                   "description":"","tags":[]}}]})
        yt=Client()
        with patch.object(tracker,"youtube",yt), \
             patch.object(tracker,"load_tracked_channels",return_value={
                 "UC-lB_sG0uviaY9pD07kiwjg":{"title":"Taylor John Hardin Reactions"}}), \
             patch.object(tracker,"KEYWORD_REPORT_ONLY",True):
            videos,_,scanned=tracker.get_reactions_with_stats()
        self.assertEqual(videos,[])
        self.assertEqual(len(scanned),1)

    def test_default_shadow_mode_off(self):
        self.assertFalse(tracker.KEYWORD_REPORT_ONLY)

if __name__=="__main__":
    unittest.main()
