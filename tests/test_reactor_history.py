"""Offline verification of history scoring from 32 prior band-relevant cases."""
import copy
import json
import unittest
from scripts.evaluate_reactor_history import (
    REGISTRY, MAP, STRONG, FIVE, EARLIER,
    _csv, build_cases, assess_all,
)


class ReactorHistoryTests(unittest.TestCase):
    def fixture(self):
        reg=json.loads(REGISTRY.read_text(encoding="utf-8"))["channels"]
        mapping=json.loads(MAP.read_text(encoding="utf-8"))
        prior=json.loads(STRONG.read_text(encoding="utf-8"))
        cases=build_cases(prior,_csv(FIVE),_csv(EARLIER))
        return reg,mapping,prior,cases

    def test_125_channels_and_human_label_totals(self):
        reg,mapping,_,cases=self.fixture()
        result=assess_all(reg,mapping,cases)
        self.assertEqual(result["channels_total"],125)
        self.assertEqual(result["band_relevant_videos_assessed"],32)
        self.assertEqual(result["verified_reaction_videos"],6)
        self.assertEqual(result["verified_band_nonreaction_videos"],4)
        self.assertEqual(result["metadata_supported_reaction_candidates"],18)
        self.assertEqual(result["metadata_assessed_nonreaction_videos"],4)
        self.assertEqual(len(result["channels"]),125)

    def test_six_distinct_verified_reactor_channels_no_two_video_trust(self):
        reg,mapping,_,cases=self.fixture()
        result=assess_all(reg,mapping,cases)
        self.assertEqual(result["channels_with_at_least_one_verified_reaction"],6)
        self.assertEqual(result["channels_with_two_or_more_verified_reactions"],0)
        self.assertTrue(all(not x["eligible_for_auto_approval"] for x in result["channels"]))

    def test_hcjb_has_one_real_reaction_and_one_naces_tribute(self):
        reg,mapping,_,cases=self.fixture()
        result=assess_all(reg,mapping,cases)
        hcjb=next(x for x in result["channels"]
                  if x["channel_id"]=="UCtEs-cB4K1XFnXgLrIWW1Zg")
        self.assertEqual(hcjb["confirmed_reactions"],1)
        self.assertEqual(hcjb["confirmed_band_nonreactions"],1)
        self.assertEqual(hcjb["history_signal"],"mixed_confirmed_content")
        self.assertEqual(hcjb["reviewed_band_related_video_ids"]["verified_reaction"],["jxoHvBlRABA"])
        self.assertEqual(hcjb["reviewed_band_related_video_ids"]["verified_band_nonreaction"],["fqTvzUxJT1k"])

    def test_taylor_vinny_cherman_global_seat_confirmed_single(self):
        reg,mapping,_,cases=self.fixture()
        result=assess_all(reg,mapping,cases)
        by={x["channel_id"]:x for x in result["channels"]}
        for cid,vid in [
            ("UC-lB_sG0uviaY9pD07kiwjg","hf03Voz2QBM"),
            ("UCahJtq3gSn-QXXTGzTaj7Fg","E2tRH0B6Ow8"),
            ("UC27_PpVaZq4kYquM5v0m3lg","JjFzKrfhNgg"),
            ("UCvEgHm5bWB8Rvla_Bxl83WA","iVeWY7mTkrE"),
            ("UC8ReQVU4PPfbxRy4B18jauw","gXWQQNUpKcA"),
        ]:
            with self.subTest(channel=cid):
                self.assertEqual(by[cid]["confirmed_reactions"],1)
                self.assertEqual(by[cid]["reviewed_band_related_video_ids"]["verified_reaction"],[vid])

    def test_metadata_only_strong30_user_matches_not_silently_certified(self):
        reg,mapping,_,cases=self.fixture()
        result=assess_all(reg,mapping,cases)
        self.assertEqual(cases["Yrt40Jti9zA"]["status"],"metadata_supported_reaction")
        self.assertEqual(cases["6Eu4f5oqXnk"]["status"],"metadata_nonreaction")
        self.assertEqual(cases["fqTvzUxJT1k"]["status"],"verified_band_nonreaction")
        self.assertIsNone(result.get("channel_precision"))
        self.assertTrue(result["no_auto_approval"])

    def test_unrelated_artist_negatives_not_counted_against_reactors(self):
        reg,mapping,_,cases=self.fixture()
        result=assess_all(reg,mapping,cases)
        self.assertEqual(result["earlier_unrelated_human_negative_controls_excluded_from_channel_trust"],44)
        t=next(x for x in result["channels"]
               if x["channel_id"]=="UC-lB_sG0uviaY9pD07kiwjg")
        self.assertEqual(t["confirmed_band_nonreactions"],0)
        # User rejected a Ren & Chris Webby video by Taylor John Hardin,
        # which must NOT be counted as a failed Missioned Souls reaction.
        self.assertNotIn("w-Lu3DfSMFc",str(t))

    def test_untagged_channels_stay_unknown_not_unreliable(self):
        reg,mapping,_,cases=self.fixture()
        result=assess_all(reg,mapping,cases)
        unknown=[x for x in result["channels"]
                 if x["history_signal"]=="insufficient_band_specific_review"]
        self.assertGreater(len(unknown),75)
        self.assertTrue(all(not x["eligible_for_auto_approval"] for x in unknown))

    def test_foreign_channel_mapping_rejected(self):
        reg,mapping,_,cases=self.fixture()
        bad=copy.deepcopy(mapping)
        bad["video_channel_ids"]["hf03Voz2QBM"]="UC0000000000000000000000"
        with self.assertRaisesRegex(ValueError,"untracked channel"):
            assess_all(reg,bad,cases)

    def test_mapping_missing_label_rejected(self):
        reg,mapping,_,cases=self.fixture()
        bad=copy.deepcopy(mapping)
        del bad["video_channel_ids"]["jxoHvBlRABA"]
        with self.assertRaisesRegex(ValueError,"disagree"):
            assess_all(reg,bad,cases)

    def test_rejected_unverified_human_followup(self):
        _,_,prior,_=self.fixture()
        rows=_csv(FIVE)
        rows[0]["reviewer"]="model"
        with self.assertRaisesRegex(ValueError,"Unverified"):
            build_cases(prior,rows,_csv(EARLIER))

    def test_earlier_negative_controls_not_imported_as_band_scope(self):
        _,_,prior,cases=self.fixture()
        self.assertEqual(len(_csv(EARLIER)),46)
        self.assertNotIn("w-Lu3DfSMFc",cases)
        self.assertNotIn("DL1NYFJg55M",cases)
        self.assertIn("E2tRH0B6Ow8",cases)
        self.assertIn("jxoHvBlRABA",cases)


if __name__=="__main__":
    unittest.main()
