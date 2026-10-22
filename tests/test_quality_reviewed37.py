"""Offline quality scoring must not label metadata-only cases as human truth."""
import json
import unittest

from scripts.score_quality_reviewed37 import (
    ADJ, FIVE, MAPPING, SEVEN_CASES, SEVEN_LABELS, _csv, build_rows, evaluate,
)


class QualityReviewed37Tests(unittest.TestCase):
    def test_37_selected_review_cases_have_unambiguous_provenance(self):
        report=evaluate()
        self.assertEqual(report["total_selected_band_related"],37)
        self.assertEqual(report["confirmed_reactions"],8)
        self.assertEqual(report["confirmed_nonreactions"],7)
        self.assertEqual(report["metadata_supported_reactions_not_video_verified"],18)
        self.assertEqual(report["metadata_assessed_nonreactions_not_video_verified"],4)
        self.assertTrue(report["no_population_metrics"])
        self.assertTrue(report["no_discord_authorization"])

    def test_no_verified_nonreaction_is_automatically_strong(self):
        report=evaluate()
        self.assertEqual(report["human_nonreaction_tiers"]["strong"],0)
        self.assertEqual(sum(report["human_nonreaction_tiers"].values()),7)

    def test_no_verified_performance_reaction_is_silently_lost(self):
        report=evaluate()
        self.assertEqual(report["human_reaction_tiers"]["none"],0)
        self.assertEqual(sum(report["human_reaction_tiers"].values()),8)

    def test_metadata_supported_not_renamed_human_verification(self):
        report=evaluate()
        item=next(r for r in report["cases"] if r["video_id"]=="Yrt40Jti9zA")
        self.assertEqual(item["truth_provenance"],"metadata_supported_reaction")
        self.assertNotIn("verified",item["truth_provenance"])

    def test_hcjb_followup_nonreactions_are_excluded_not_verdict_reversed(self):
        report=evaluate()
        rows={r["video_id"]:r for r in report["cases"]}
        for vid in ("bRpiOxl1sdE","pbgQdCCaPo8","7iYFOFiu1KI"):
            with self.subTest(video=vid):
                self.assertEqual(rows[vid]["truth_provenance"],"verified_nonreaction")
                self.assertEqual(rows[vid]["classifier_tier"],"none")

    def test_reactor_history_without_new_reviews_not_automatically_trusted(self):
        report=evaluate()
        rows={r["video_id"]:r for r in report["cases"]}
        # User-confirmed studio-cover reaction is still manual review based
        # on title and authenticated independent uploading channel.
        self.assertEqual(rows["gXWQQNUpKcA"]["truth_provenance"],"verified_reaction")
        self.assertEqual(rows["gXWQQNUpKcA"]["classifier_tier"],"review")
        self.assertFalse(rows["gXWQQNUpKcA"]["auto_candidate_is_not_send_permission"])

    def test_corpus_mismatch_is_fatal_not_silently_skipped(self):
        adj=json.loads(ADJ.read_text())
        mp=json.loads(MAPPING.read_text())
        seven=json.loads(SEVEN_CASES.read_text())
        with self.assertRaisesRegex(ValueError,"missing cases"):
            build_rows(adj,_csv(FIVE)[:-1],mp,seven,_csv(SEVEN_LABELS))
        bad=json.loads(MAPPING.read_text())
        del bad["video_channel_ids"]["hf03Voz2QBM"]
        with self.assertRaisesRegex(ValueError,"Unmapped|unmapped"):
            build_rows(adj,_csv(FIVE),bad,seven,_csv(SEVEN_LABELS))


if __name__=="__main__":
    unittest.main()
