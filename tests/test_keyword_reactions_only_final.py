"""Five user-labelled unresolved videos must reconcile cleanly with saved 30."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.score_keyword_reactions_only_final import (
    EXPECTED_IDS, HUMAN_FIVE, final_evaluation, read_five_labels,
)
from scripts.score_keyword_reactions_only import ADJUDICATION, evaluate as earlier
from scripts.score_keyword_strong30 import LABELS, SELECTION, load_labels


class FinalUserVerificationTests(unittest.TestCase):
    def inputs(self):
        labels=read_five_labels()
        prior=earlier(
            json.loads(ADJUDICATION.read_text(encoding="utf-8")),
            json.loads(SELECTION.read_text(encoding="utf-8")),
            load_labels(LABELS)
        )
        return prior,labels

    def test_five_exact_previous_unresolved_video_ids(self):
        prior,labels=self.inputs()
        unresolved={r["video_id"] for r in prior["cases"]
                    if r["reactions_only_assessment"]=="recheck_video"}
        self.assertEqual(set(labels),unresolved)
        self.assertEqual(unresolved,EXPECTED_IDS)

    def test_all_five_human_classifications_match_submission(self):
        _,labels=self.inputs()
        self.assertEqual({vid:row["content_type"] for vid,row in labels.items()},
                         {"VhwqMoL-t8A":"repost",
                          "cx1s0V_8Bu4":"discussion",
                          "JjFzKrfhNgg":"reaction",
                          "iVeWY7mTkrE":"reaction",
                          "gXWQQNUpKcA":"reaction"})

    def test_summary_is_22_reaction_candidates_8_excluded_zero_unresolved(self):
        prior,labels=self.inputs()
        r=final_evaluation(prior,labels)
        self.assertEqual(r["earlier_metadata_supported_reactions"],19)
        self.assertEqual(r["new_human_verified_reactions"],3)
        self.assertEqual(r["earlier_metadata_supported_exclusions"],6)
        self.assertEqual(r["new_human_verified_nonreactions"],2)
        self.assertEqual(r["total_reaction_candidates"],22)
        self.assertEqual(r["total_excluded_nonreactions"],8)
        self.assertEqual(r["unresolved_count"],0)
        self.assertEqual(r["total"],30)

    def test_provisional_data_not_rewritten_and_no_independent_playback_claim(self):
        prior,labels=self.inputs()
        r=final_evaluation(prior,labels)
        source={x["video_id"]:x for x in prior["cases"]}
        updated={x["video_id"]:x for x in r["cases"]}
        self.assertEqual(source["cx1s0V_8Bu4"]["reactions_only_assessment"],
                         "recheck_video")
        self.assertEqual(updated["cx1s0V_8Bu4"]["reactions_only_assessment"],
                         "human_verified_nonreaction")
        self.assertFalse(updated["cx1s0V_8Bu4"]["verified_by_new_video_watch"])
        self.assertTrue(updated["cx1s0V_8Bu4"]["human_followup_label_received"])
        self.assertEqual(updated["fqTvzUxJT1k"]["raw_user_label"],"match")
        self.assertIn("Not an actual reaction",
                      updated["fqTvzUxJT1k"]["raw_user_note"])

    def test_all_excluded_cases_are_not_high_confidence(self):
        prior,labels=self.inputs()
        r=final_evaluation(prior,labels)
        invalid=[x for x in r["cases"] if x["reactions_only_assessment"]
                 in ("exclude_by_reactions_only_policy","human_verified_nonreaction")]
        self.assertEqual(len(invalid),8)
        self.assertTrue(all(x["new_keyword_tier"]!="strong" for x in invalid))

    def test_three_new_reactions_still_eligible_for_review(self):
        prior,labels=self.inputs()
        r=final_evaluation(prior,labels)
        for id in ("JjFzKrfhNgg","iVeWY7mTkrE","gXWQQNUpKcA"):
            result=next(x for x in r["cases"] if x["video_id"]==id)
            self.assertEqual(result["reactions_only_assessment"],"human_verified_reaction")
            self.assertEqual(result["new_keyword_tier"],"review")

    def test_no_auto_post_and_no_population_precision_claim(self):
        prior,labels=self.inputs()
        r=final_evaluation(prior,labels)
        self.assertIsNone(r["global_population_precision"])
        self.assertIsNone(r["global_population_recall"])
        self.assertTrue(all(x["would_auto_post"] is False for x in r["cases"]))

    def test_nonhuman_or_wrong_video_ids_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"bad.csv"
            with path.open("w",newline="",encoding="utf-8") as f:
                w=csv.writer(f)
                w.writerow(("video_id","content_type","reviewer","notes"))
                w.writerow(("VhwqMoL-t8A","repost","ai",""))
            with self.assertRaisesRegex(ValueError,"Invalid human"):
                read_five_labels(path)
            with path.open("w",newline="",encoding="utf-8") as f:
                w=csv.writer(f)
                w.writerow(("video_id","content_type","reviewer","notes"))
                w.writerow(("abcdefghXYZ","reaction","human",""))
            with self.assertRaisesRegex(ValueError,"IDs must match"):
                read_five_labels(path)

    def test_ineligible_known_video_id_cannot_be_overwritten(self):
        prior,labels=self.inputs()
        labels.pop("VhwqMoL-t8A")
        labels["fqTvzUxJT1k"]={"video_id":"fqTvzUxJT1k",
                                "content_type":"reaction","reviewer":"human","notes":""}
        with self.assertRaisesRegex(ValueError,"Only the five"):
            final_evaluation(prior,labels)

    def test_freeze_integrity_rejects_missing_human_label(self):
        prior,labels=self.inputs()
        labels.pop("cx1s0V_8Bu4")
        with self.assertRaises(ValueError):
            final_evaluation(prior,labels)


if __name__=="__main__":
    unittest.main()
