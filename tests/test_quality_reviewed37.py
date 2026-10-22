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


    def test_new_review_queue_has_no_fabricated_human_verdicts(self):
        from pathlib import Path
        file=Path(__file__).resolve().parents[1]/"data"/"reaction_quality_next8_pending_review_20261022.json"
        data=json.loads(file.read_text())
        cases=data["items"]
        self.assertEqual(len(cases),8)
        self.assertEqual(len({c["video_id"] for c in cases}),8)
        self.assertTrue(all(c["review_label"] is None for c in cases))
        self.assertFalse(data["auto_posting"])
        self.assertEqual(data["youtube_api_calls"],0)
        self.assertEqual(sum(c["metadata_status_before_human_review"]=="supported_reaction" for c in cases),6)
        self.assertEqual(sum(c["metadata_status_before_human_review"]=="exclude_by_reactions_only_policy" for c in cases),2)
        self.assertIn("not random",data["selection_warning"])

    def test_new_review_queue_does_not_duplicate_human_verified_subset(self):
        from pathlib import Path
        data=json.loads((Path(__file__).resolve().parents[1]/"data"/
                         "reaction_quality_next8_pending_review_20261022.json").read_text())
        current=evaluate()
        historical={c["video_id"]:c for c in current["cases"]}
        for case in data["items"]:
            with self.subTest(video=case["video_id"]):
                self.assertIn(case["video_id"],historical)
                self.assertIn(historical[case["video_id"]]["truth_provenance"],
                              ("metadata_supported_reaction","metadata_assessed_nonreaction"))
                self.assertEqual(case["url"],
                                 "https://www.youtube.com/watch?v="+case["video_id"])


if __name__=="__main__":
    unittest.main()
