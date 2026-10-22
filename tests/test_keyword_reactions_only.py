"""Reactions-only user policy and no-auto-post regression suite."""
import json
import unittest
from scripts.score_keyword_strong30 import LABELS, SELECTION, load_labels
from scripts.score_keyword_reactions_only import ADJUDICATION, evaluate
from keyword_detector import classify


class ReactionOnlyScopeTests(unittest.TestCase):
    def report(self):
        a=json.loads(ADJUDICATION.read_text(encoding="utf-8"))
        s=json.loads(SELECTION.read_text(encoding="utf-8"))
        return evaluate(a,s,load_labels(LABELS))

    def test_all_thirty_preserved_and_categorised(self):
        r=self.report()
        self.assertEqual(r["total"],30)
        self.assertEqual(r["metadata_supported_reaction_count"],19)
        self.assertEqual(r["metadata_excluded_nonreaction_count"],6)
        self.assertEqual(r["requires_video_recheck_count"],5)

    def test_original_human_labels_not_rewritten(self):
        r=self.report()
        self.assertEqual(r["original_user_match_count"],29)
        self.assertEqual(r["original_user_not_match_count"],1)
        n=next(x for x in r["cases"] if x["video_id"]=="fqTvzUxJT1k")
        self.assertEqual(n["raw_user_label"],"match")
        self.assertIn("Not an actual reaction",n["raw_user_note"])
        self.assertEqual(n["reactions_only_assessment"],"exclude_by_reactions_only_policy")

    def test_paper_dove_and_tribute_are_excluded_from_any_auto_alert(self):
        r=self.report()
        for id in ("r5TVQYSmZcY","fqTvzUxJT1k"):
            row=next(x for x in r["cases"] if x["video_id"]==id)
            self.assertEqual(row["reactions_only_assessment"],
                             "exclude_by_reactions_only_policy")
            self.assertEqual(row["new_keyword_tier"],"none")
            self.assertFalse(row["would_auto_post"])

    def test_concert_and_website_milestone_excluded(self):
        r=self.report()
        for id in ("6Eu4f5oqXnk","IjaXJDLaIEM","qqhUL5a0rik","vpYHLQr04LQ"):
            row=next(x for x in r["cases"] if x["video_id"]==id)
            self.assertEqual(row["new_keyword_tier"],"none")

    def test_five_uncertain_reaction_archive_and_covers_never_strong(self):
        r=self.report()
        unresolved={x["video_id"]:x for x in r["cases"]
                    if x["reactions_only_assessment"]=="recheck_video"}
        self.assertEqual(set(unresolved),{
            "VhwqMoL-t8A","cx1s0V_8Bu4","JjFzKrfhNgg",
            "iVeWY7mTkrE","gXWQQNUpKcA"})
        self.assertTrue(all(x["new_keyword_tier"]!="strong" for x in unresolved.values()))

    def test_explicit_performance_reactions_still_get_strong_or_review(self):
        r=self.report()
        strong=[x for x in r["cases"]
                if x["reactions_only_assessment"]=="supported_reaction"
                and x["new_keyword_tier"]=="strong"]
        self.assertGreaterEqual(len(strong),10)
        self.assertEqual(next(x for x in r["cases"]
                              if x["video_id"]=="hf03Voz2QBM")["new_keyword_tier"],"strong")

    def test_no_population_precision_recall_or_new_verified_truth_claim(self):
        r=self.report()
        self.assertIsNone(r["population_precision"])
        self.assertIsNone(r["population_recall"])
        self.assertFalse(r["new_human_verified_ground_truth"])
        self.assertTrue(all(not x["verified_by_new_video_watch"] for x in r["cases"]))

    def test_tampered_assessment_rejected_when_unsafe(self):
        a=json.loads(ADJUDICATION.read_text(encoding="utf-8"))
        s=json.loads(SELECTION.read_text(encoding="utf-8"))
        a["items"][0]["status"]="exclude_by_reactions_only_policy"
        with self.assertRaisesRegex(ValueError,"Unsafe high-confidence"):
            evaluate(a,s,load_labels(LABELS))

    def test_unknown_or_missing_video_id_rejected(self):
        a=json.loads(ADJUDICATION.read_text(encoding="utf-8"))
        s=json.loads(SELECTION.read_text(encoding="utf-8"))
        a["items"][0]["video_id"]="abcdefghXYZ"
        with self.assertRaisesRegex(ValueError,"agree exactly"):
            evaluate(a,s,load_labels(LABELS))

    def test_reactor_description_boilerplate_not_enough_for_strong(self):
        r=classify("Missioned Souls Rosanna family band studio cover",
                   "Subscribe to my reaction channel for lots of reactions!",
                   ["missionedsoulsreaction"])
        self.assertEqual(r["tier"],"review")
        self.assertFalse(r["auto_candidate"])


if __name__=="__main__":
    unittest.main()
