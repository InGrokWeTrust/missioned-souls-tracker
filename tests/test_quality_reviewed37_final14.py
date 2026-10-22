"""Regression: exact 13/1 final human review, frozen predecessors and disagreement."""
import copy
import json
import unittest

from scripts.score_quality_reviewed37_followup import evaluate_followup
from scripts.score_quality_reviewed37_final14 import (
    EXPECTED_REACTIONS, EXPECTED_NONREACTIONS,
    PENDING, HUMAN, _csv, apply_final_review, evaluate_final,
)
from scripts.score_quality_reviewed37 import ROOT


class FinalFourteenHumanReviewTests(unittest.TestCase):
    def inputs(self):
        return (evaluate_followup(), json.loads(PENDING.read_text(encoding="utf-8")),
                _csv(HUMAN))

    def test_exact_labels_no_inferred_overrides(self):
        old, pending, human = self.inputs()
        labels = {row["video_id"]:row["review_label"] for row in human}
        self.assertEqual(len(human),14)
        self.assertEqual(len(labels),14)
        self.assertEqual(set(labels), EXPECTED_REACTIONS|EXPECTED_NONREACTIONS)
        self.assertEqual({key for key,val in labels.items() if val == "reaction"},EXPECTED_REACTIONS)
        self.assertEqual({key for key,val in labels.items() if val == "not_match"},EXPECTED_NONREACTIONS)
        self.assertTrue(all(row["reviewer"] == "human" and row["notes"] == "" for row in human))
        self.assertEqual(labels["IjaXJDLaIEM"],"reaction")
        self.assertEqual(labels["qqhUL5a0rik"],"not_match")

    def test_full_audit_updated_without_double_counting_same_37(self):
        result=evaluate_final()
        self.assertEqual(result["total_selected_band_related"],37)
        self.assertEqual(len({row["video_id"] for row in result["cases"]}),37)
        self.assertEqual(result["confirmed_reactions"],27)
        self.assertEqual(result["confirmed_nonreactions"],10)
        self.assertEqual(result["metadata_supported_reactions_not_video_verified"],0)
        self.assertEqual(result["metadata_assessed_nonreactions_not_video_verified"],0)
        self.assertEqual(len(result["final_fourteen_verdicts"]),14)
        self.assertEqual(sum(result["human_reaction_tiers"].values()),27)
        self.assertEqual(sum(result["human_nonreaction_tiers"].values()),10)

    def test_exactly_one_metadata_human_disagreement_untouched_classifier(self):
        result=evaluate_final()
        self.assertEqual(result["human_metadata_disagreement_count"],1)
        self.assertEqual([x["video_id"] for x in result["human_metadata_disagreements"]],
                         ["IjaXJDLaIEM"])
        self.assertIn("IjaXJDLaIEM",result["confirmed_reactions_excluded_by_classifier"])
        self.assertEqual(result["strong_false_positive_video_ids"],[])
        self.assertEqual(result["original_metadata_exclusions_confirmed_nonreaction"],["qqhUL5a0rik"])

    def test_concert_not_match_is_still_excluded(self):
        cases={x["video_id"]:x for x in evaluate_final()["cases"]}
        row=cases["qqhUL5a0rik"]
        self.assertEqual(row["truth_provenance"],"verified_nonreaction")
        self.assertEqual(row["previous_metadata_provenance"],"metadata_assessed_nonreaction")
        self.assertEqual(row["classifier_tier"],"none")
        self.assertFalse(row["auto_candidate_is_not_send_permission"])
        self.assertFalse(row["human_metadata_disagreement"])

    def test_potential_missed_reaction_preserves_original_exclusion(self):
        cases={x["video_id"]:x for x in evaluate_final()["cases"]}
        row=cases["IjaXJDLaIEM"]
        self.assertEqual(row["truth_provenance"],"verified_reaction")
        self.assertEqual(row["previous_metadata_provenance"],"metadata_assessed_nonreaction")
        self.assertEqual(row["classifier_tier"],"none")
        self.assertTrue(row["human_metadata_disagreement"])
        self.assertFalse(row["auto_candidate_is_not_send_permission"])

    def test_no_mutation_of_old_audit_or_pending_queue(self):
        prior,pending,human=self.inputs()
        before_prior=copy.deepcopy(prior)
        before_pending=copy.deepcopy(pending)
        result=apply_final_review(prior,pending,human)
        self.assertEqual(prior,before_prior)
        self.assertEqual(pending,before_pending)
        self.assertTrue(all(row["review_label"] is None for row in pending["items"]))
        for row in result["cases"]:
            existing=next(x for x in prior["cases"] if x["video_id"]==row["video_id"])
            self.assertEqual(existing["classifier_tier"],row["classifier_tier"])
            self.assertEqual(existing["review_priority"],row["review_priority"])
            self.assertEqual(existing["auto_candidate_is_not_send_permission"],
                             row["auto_candidate_is_not_send_permission"])

    def test_wrong_label_or_reviewer_rejected(self):
        old,pending,human=self.inputs()
        bad=copy.deepcopy(human)
        bad[-1]["review_label"]="reaction"
        with self.assertRaisesRegex(ValueError,"latest explicit submission"):
            apply_final_review(old,pending,bad)
        bad=copy.deepcopy(human)
        bad[0]["reviewer"]="model"
        with self.assertRaisesRegex(ValueError,"latest explicit submission"):
            apply_final_review(old,pending,bad)

    def test_missing_duplicate_or_unexpected_id_rejected(self):
        old,pending,human=self.inputs()
        with self.assertRaisesRegex(ValueError,"exactly 14"):
            apply_final_review(old,pending,human[:-1])
        with self.assertRaisesRegex(ValueError,"exactly 14"):
            apply_final_review(old,pending,human+[human[0]])
        bad=copy.deepcopy(human)
        bad[0]["video_id"]="fake-video-id"
        with self.assertRaisesRegex(ValueError,"exactly 14"):
            apply_final_review(old,pending,bad)

    def test_changed_channel_or_original_verdict_rejected(self):
        old,pending,human=self.inputs()
        bad=copy.deepcopy(pending)
        bad["items"][0]["channel_id"]="UCnot-same"
        with self.assertRaisesRegex(ValueError,"uploading channel"):
            apply_final_review(old,bad,human)
        bad=copy.deepcopy(old)
        next(x for x in bad["cases"] if x["video_id"]=="IjaXJDLaIEM")[
            "truth_provenance"]="metadata_supported_reaction"
        with self.assertRaisesRegex(ValueError,"metadata provenance"):
            apply_final_review(bad,pending,human)

    def test_no_production_sender_change_or_discord_authorisation(self):
        result=evaluate_final()
        self.assertTrue(result["no_discord_authorization"])
        self.assertTrue(result["no_population_metrics"])
        self.assertTrue(result["earlier_evidence_unchanged"])
        self.assertIn("has not watched",result["reviewer"])
        source=(ROOT/"autoreactions.py").read_text(encoding="utf-8")
        self.assertNotIn("from keyword_detector import",source)
        code=(ROOT/"scripts"/"score_quality_reviewed37_final14.py").read_text(encoding="utf-8")
        self.assertNotIn("post_webhook(",code)
        self.assertNotIn("requests.get(",code)


if __name__=="__main__":
    unittest.main()
