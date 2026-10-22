"""Stage quality rules offline; verified reviewer labels are never inferred from titles."""
import csv
import json
import unittest
from pathlib import Path

from keyword_detector import classify, EXCLUDED_REACTOR_CHANNEL_IDS

ROOT=Path(__file__).resolve().parents[1]
SELECTED=ROOT/"data"/"reactor_history_next7_pending_review_20261022.json"
REVIEWS=ROOT/"data"/"reactor_history_next7_human_labels_20261022.csv"
HCJB="UCtEs-cB4K1XFnXgLrIWW1Zg"
GLOBAL="UC8ReQVU4PPfbxRy4B18jauw"


class ReactionQualityShadowTests(unittest.TestCase):
    def cases(self):
        source=json.loads(SELECTED.read_text(encoding="utf-8"))
        with REVIEWS.open(encoding="utf-8",newline="") as f:
            labels={row["video_id"]:row for row in csv.DictReader(f)}
        return source["items"],labels

    def test_every_new_video_has_human_verdict_and_exact_id(self):
        cases,labels=self.cases()
        self.assertEqual(len(cases),7)
        self.assertEqual(len(labels),7)
        self.assertEqual({c["video_id"] for c in cases},set(labels))
        self.assertTrue(all(x["reviewer"]=="human" for x in labels.values()))

    def test_hcjb_is_excluded_by_exact_channel_id_regardless_of_title(self):
        cases,labels=self.cases()
        self.assertIn(HCJB,EXCLUDED_REACTOR_CHANNEL_IDS)
        for c in cases:
            if c["channel_id"] != HCJB:continue
            self.assertEqual(labels[c["video_id"]]["review_label"],"not_match")
            result=classify(c["title"],channel_id=c["channel_id"],
                            channel_name=c["channel"],verified_reactor_source=True)
            self.assertEqual(result["tier"],"none")
            self.assertFalse(result["auto_candidate"])
            self.assertEqual(result["review_priority"],"none")

    def test_nonreaction_dedicated_song_is_not_a_high_priority_review(self):
        result=classify("A Dedicated Song for Neisha of Missioned Souls",
                        channel_id=GLOBAL,channel_name="Global take",
                        verified_reactor_source=True)
        self.assertEqual(result["tier"],"none")
        self.assertFalse(result["auto_candidate"])

    def test_confirmed_positive_titles_remain_evidence_not_verified_by_detector(self):
        cases,labels=self.cases()
        positives=[c for c in cases if labels[c["video_id"]]["review_label"]=="reaction"]
        self.assertEqual(len(positives),4)
        for c in positives:
            result=classify(c["title"],channel_id=c["channel_id"],
                            channel_name=c["channel"],verified_reactor_source=True)
            with self.subTest(video=c["video_id"]):
                self.assertEqual(result["tier"],"strong")
                self.assertTrue(result["auto_candidate"])
                self.assertEqual(result["review_priority"],"none")
        # auto_candidate means strong *shadow evidence*, not a Discord permission.

    def test_unlabelled_cover_title_has_high_manual_review_priority_only(self):
        result=classify("ROSANNA by Toto | Missioned Souls | Familly band studio cover",
                        channel_id=GLOBAL,channel_name="Global take",
                        verified_reactor_source=True)
        self.assertEqual(result["tier"],"review")
        self.assertEqual(result["review_priority"],"high")
        self.assertFalse(result["auto_candidate"])

    def test_titled_general_band_discussion_never_auto_candidates(self):
        result=classify("Missioned Souls - What makes them Different From Other Family Bands",
                        channel_id="UCv8D6RWgYu5FydqkNcv9gpg",
                        channel_name="ONE*A*BEES FAM",verified_reactor_source=True)
        self.assertEqual(result["tier"],"review")
        self.assertFalse(result["auto_candidate"])
        # Ambiguous cases still require actual human review.

    def test_unverified_uploading_source_does_not_gain_priority(self):
        result=classify("Missioned Souls | Familly band studio cover",
                        channel_id=GLOBAL,channel_name="Global take",
                        verified_reactor_source=False)
        self.assertEqual(result["tier"],"review")
        self.assertEqual(result["review_priority"],"normal")

    def test_official_band_upload_is_not_third_party_reaction(self):
        result=classify("Missioned Souls | reaction to Crazy Train",
                        channel_id="UCeTfwcnUVurw7Qrk_KF5Idw",
                        verified_reactor_source=True)
        self.assertEqual(result["tier"],"none")

    def test_archive_reposts_blocked(self):
        result=classify('MISSIONED SOULS COVERS "TAKE ON ME"',
                        channel_name="MISSIONED SOULS REACTION ARCHIVE",
                        channel_id="UCcEVJqIb_6FD30Ipk1lkbRw",
                        verified_reactor_source=True)
        self.assertEqual(result["tier"],"none")

    def test_paper_dove_cannot_be_promoted_by_description_or_uploader(self):
        result=classify("Paper Dove with Neisha playing guitar",
                        description="Missioned Souls tribute",channel_id=GLOBAL,
                        channel_name="Global take",verified_reactor_source=True)
        self.assertEqual(result["tier"],"none")

    def test_shadow_quality_rules_not_wired_to_production_sender(self):
        source=(ROOT/"autoreactions.py").read_text(encoding="utf-8")
        self.assertNotIn("from keyword_detector import",source)
        self.assertNotIn("KEYWORD_REPORT_ONLY",source)
        self.assertNotIn("report_unmatched(",source)

    def test_no_live_api_or_discord_in_classifier(self):
        src=(ROOT/"keyword_detector.py").read_text(encoding="utf-8")
        self.assertNotIn("import requests",src)
        self.assertNotIn("post_webhook(",src)


if __name__=="__main__":
    unittest.main()
