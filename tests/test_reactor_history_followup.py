"""Regress the seven human labels and HCJB no-alert opt-out."""
import json
import unittest
from keyword_detector import classify, EXCLUDED_REACTOR_CHANNEL_IDS
from scripts.evaluate_reactor_history_followup import (
    evaluate, HCJB, PENDING, LABELS, merge_review,
)
from scripts.evaluate_reactor_history import (
    STRONG, FIVE, EARLIER, MAP, build_cases, _csv
)

class UserReactorFollowupTests(unittest.TestCase):
    def test_exact_seven_user_verdicts(self):
        labels={r["video_id"]:r["review_label"] for r in _csv(LABELS)}
        self.assertEqual(labels,{
            "bRpiOxl1sdE":"not_match",
            "pbgQdCCaPo8":"not_match",
            "7iYFOFiu1KI":"not_match",
            "EN0kaZai6T4":"reaction",
            "ayffX_R86kw":"reaction",
            "XBYttS5x4i4":"reaction",
            "b6eDBjF6sko":"reaction",
        })

    def test_hcjb_history_one_true_and_four_band_nonreactions(self):
        report=evaluate()
        row=next(x for x in report["channels"] if x["channel_id"]==HCJB)
        self.assertEqual(row["confirmed_reactions"],1)
        self.assertEqual(row["confirmed_band_nonreactions"],4)
        self.assertTrue(row["user_excluded_from_experimental_reaction_discovery"])
        self.assertFalse(row["eligible_for_auto_approval"])
        self.assertIn("jxoHvBlRABA",row["reviewed_band_related_video_ids"]["verified_reaction"])
        for vid in ("fqTvzUxJT1k","bRpiOxl1sdE","pbgQdCCaPo8","7iYFOFiu1KI"):
            self.assertIn(vid,row["reviewed_band_related_video_ids"]["verified_band_nonreaction"])

    def test_vinny_and_global_take_history(self):
        report=evaluate()
        by={x["channel_id"]:x for x in report["channels"]}
        vinny=by["UCahJtq3gSn-QXXTGzTaj7Fg"]
        globaltake=by["UC8ReQVU4PPfbxRy4B18jauw"]
        self.assertEqual((vinny["confirmed_reactions"],vinny["confirmed_band_nonreactions"]),(2,0))
        self.assertEqual((globaltake["confirmed_reactions"],globaltake["confirmed_band_nonreactions"]),(4,0))
        self.assertFalse(vinny["user_excluded_from_experimental_reaction_discovery"])
        self.assertFalse(globaltake["user_excluded_from_experimental_reaction_discovery"])

    def test_complete_125_channel_39_video_counts(self):
        report=evaluate()
        self.assertEqual(report["channels_total"],125)
        self.assertEqual(report["band_relevant_videos_assessed"],39)
        self.assertEqual(report["verified_reaction_videos"],10)
        self.assertEqual(report["verified_band_nonreaction_videos"],7)
        self.assertEqual(report["metadata_supported_reaction_candidates"],18)
        self.assertEqual(report["metadata_assessed_nonreaction_videos"],4)
        self.assertEqual(report["channels_with_two_or_more_verified_reactions"],2)
        self.assertEqual(report["channels_with_at_least_one_verified_reaction"],6)
        self.assertTrue(all(not r["eligible_for_auto_approval"] for r in report["channels"]))

    def test_hcjb_exclusion_by_exact_id_not_name_guess(self):
        out=classify("Reacting to Missioned Souls - Rosanna",channel_id=HCJB,
                     channel_name="HCJB Music Reaction",verified_reactor_source=True)
        self.assertEqual(out["tier"],"none")
        self.assertFalse(out["auto_candidate"])
        self.assertEqual(out["review_priority"],"none")
        self.assertIn("user excluded"," ".join(out["reasons"]))
        spoof=classify("Reacting to Missioned Souls - Rosanna",
                       channel_name="HCJB Music Reaction",
                       channel_id="UC8ReQVU4PPfbxRy4B18jauw",
                       verified_reactor_source=True)
        self.assertEqual(spoof["tier"],"strong")

    def test_hcjb_historical_real_reaction_still_preserved(self):
        report=evaluate()
        hcjb=next(x for x in report["channels"] if x["channel_id"]==HCJB)
        self.assertIn("jxoHvBlRABA",hcjb["reviewed_band_related_video_ids"]["verified_reaction"])
        self.assertIn(HCJB,EXCLUDED_REACTOR_CHANNEL_IDS)

    def test_duplicate_or_unreviewed_id_rejected(self):
        prior=build_cases(json.loads(STRONG.read_text(encoding="utf-8")),
                          _csv(FIVE),_csv(EARLIER))
        mapping=json.loads(MAP.read_text(encoding="utf-8"))
        pending=json.loads(PENDING.read_text(encoding="utf-8"))
        good=_csv(LABELS)
        with self.assertRaisesRegex(ValueError,"precisely 7"):
            merge_review(prior,mapping,pending,good[:-1])
        altered=[dict(x) for x in good]
        altered[0]["reviewer"]="model"
        with self.assertRaisesRegex(ValueError,"nonhuman"):
            merge_review(prior,mapping,pending,altered)
        altered=[dict(x) for x in good]
        altered[0]["video_id"]="jxoHvBlRABA"
        with self.assertRaisesRegex(ValueError,"differ"):
            merge_review(prior,mapping,pending,altered)

    def test_no_population_accuracy_claim_from_selected_sample(self):
        r=evaluate()
        self.assertIn("Not random",r["sample_warning"])
        self.assertNotIn("population_precision",r)
        self.assertTrue(r["all_history_kept_even_for_user_excluded_channels"])

if __name__=="__main__":
    unittest.main()
