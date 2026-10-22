"""Human review round two: no fabricated labels, no overwritten old evidence."""
import copy
import json
import unittest

from scripts.score_quality_reviewed37 import evaluate as baseline_evaluate, _csv
from scripts.score_quality_reviewed37_followup import (
    EXPECTED_HUMAN, HUMAN, PENDING, evaluate_followup, overlay_review,
)


class QualityReviewNextEightTests(unittest.TestCase):
    def fixture(self):
        return (baseline_evaluate(),
                json.loads(PENDING.read_text(encoding="utf-8")),
                _csv(HUMAN))

    def test_exact_eight_human_verdicts_preserved(self):
        old,pending,labels=self.fixture()
        self.assertEqual(len(labels),8)
        self.assertEqual({x["video_id"]:x["review_label"] for x in labels},EXPECTED_HUMAN)
        self.assertEqual(sum(x["review_label"]=="reaction" for x in labels),6)
        self.assertEqual(sum(x["review_label"]=="not_match" for x in labels),2)
        self.assertTrue(all(x["reviewer"]=="human" for x in labels))

    def test_37_unique_videos_before_and_after_review(self):
        old,pending,labels=self.fixture()
        updated=overlay_review(old,pending,labels)
        self.assertEqual(len(updated["cases"]),37)
        self.assertEqual({x["video_id"] for x in old["cases"]},
                         {x["video_id"] for x in updated["cases"]})
        self.assertEqual(len(updated["eight_human_followup_overlays"]),8)

    def test_upgraded_human_counts_and_provisional_counts(self):
        updated=evaluate_followup()
        self.assertEqual(updated["confirmed_reactions"],14)
        self.assertEqual(updated["confirmed_nonreactions"],9)
        self.assertEqual(updated["metadata_supported_reactions_not_video_verified"],12)
        self.assertEqual(updated["metadata_assessed_nonreactions_not_video_verified"],2)
        self.assertEqual(updated["total_selected_band_related"],37)

    def test_rejected_concert_and_website_promos_stay_excluded(self):
        by={x["video_id"]:x for x in evaluate_followup()["cases"]}
        for vid in ("6Eu4f5oqXnk","vpYHLQr04LQ"):
            with self.subTest(video=vid):
                self.assertEqual(by[vid]["truth_provenance"],"verified_nonreaction")
                self.assertEqual(by[vid]["classifier_tier"],"none")
                self.assertFalse(by[vid]["auto_candidate_is_not_send_permission"])

    def test_six_new_genuine_reactions_not_dropped(self):
        by={x["video_id"]:x for x in evaluate_followup()["cases"]}
        for vid in [k for k,v in EXPECTED_HUMAN.items() if v=="reaction"]:
            with self.subTest(video=vid):
                self.assertEqual(by[vid]["truth_provenance"],"verified_reaction")
                self.assertIn(by[vid]["classifier_tier"],("strong","review"))
                self.assertNotEqual(by[vid]["classifier_tier"],"none")

    def test_only_label_provenance_changes_not_evidence_classification(self):
        old,pending,labels=self.fixture()
        newer=overlay_review(old,pending,labels)
        prior={x["video_id"]:x for x in old["cases"]}
        for entry in newer["cases"]:
            old_row=prior[entry["video_id"]]
            self.assertEqual(entry["classifier_tier"],old_row["classifier_tier"])
            self.assertEqual(entry["review_priority"],old_row["review_priority"])
            self.assertEqual(entry["auto_candidate_is_not_send_permission"],
                             old_row["auto_candidate_is_not_send_permission"])
            if entry["video_id"] not in EXPECTED_HUMAN:
                self.assertEqual(entry,old_row)

    def test_old_report_and_blank_review_queue_unchanged(self):
        old,pending,labels=self.fixture()
        old_copy=copy.deepcopy(old)
        pending_copy=copy.deepcopy(pending)
        overlay_review(old,pending,labels)
        self.assertEqual(old,old_copy)
        self.assertEqual(pending,pending_copy)
        self.assertTrue(all(x["review_label"] is None for x in pending["items"]))

    def test_cannot_inject_extra_label_or_duplicate(self):
        old,pending,labels=self.fixture()
        with self.assertRaisesRegex(ValueError,"exactly 8"):
            overlay_review(old,pending,labels[:-1])
        with self.assertRaisesRegex(ValueError,"exactly 8"):
            overlay_review(old,pending,labels+[labels[0]])
        bad=[dict(x) for x in labels]
        bad[0]["video_id"]="unlisted123"
        with self.assertRaisesRegex(ValueError,"Unexpected human verdict"):
            overlay_review(old,pending,bad)

    def test_cannot_overwrite_user_verdict_or_channel_id(self):
        old,pending,labels=self.fixture()
        bad=[dict(x) for x in labels]
        bad[0]["review_label"]="not_match"
        with self.assertRaisesRegex(ValueError,"explicit human verdict"):
            overlay_review(old,pending,bad)
        bad=[dict(x) for x in labels]
        bad[0]["reviewer"]="model"
        with self.assertRaisesRegex(ValueError,"explicit human verdict"):
            overlay_review(old,pending,bad)
        bad_pending=copy.deepcopy(pending)
        bad_pending["items"][0]["channel_id"]="UCfake"
        with self.assertRaisesRegex(ValueError,"Changed uploader"):
            overlay_review(old,bad_pending,labels)

    def test_review_report_cannot_authorize_production_delivery(self):
        result=evaluate_followup()
        self.assertTrue(result["no_discord_authorization"])
        self.assertTrue(result["no_population_metrics"])
        self.assertTrue(result["baseline_remains_unmodified"])
        self.assertIn("did not independently watch",result["reviewer"])
        self.assertNotIn("precision",result)

if __name__=="__main__":
    unittest.main()
