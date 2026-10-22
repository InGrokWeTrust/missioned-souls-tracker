"""Offline quality checks for the 46 actual human-review labels."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.score_keyword_human_review import (
    LABELS, SNAPSHOT, read_human_labels, score,
)


class HumanReviewRegression(unittest.TestCase):
    def load_case(self):
        labels = read_human_labels(LABELS)
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        return labels, snapshot

    def test_all_46_user_supplied_labels_match_exact_audit_ids(self):
        labels, snapshot = self.load_case()
        self.assertEqual(len(labels), 46)
        self.assertEqual(len(snapshot["rows"]), 46)
        self.assertEqual(set(labels), {r["video_id"] for r in snapshot["rows"]})

    def test_two_positive_reviews_and_44_negatives(self):
        labels, snapshot = self.load_case()
        outcome = score(snapshot, labels)
        self.assertEqual(outcome["human_matches"], 2)
        self.assertEqual(outcome["human_nonmatches"], 44)
        self.assertEqual(outcome["matched_by_review_tier"], 2)
        self.assertEqual(outcome["unrelated_by_review_tier"], 0)
        self.assertEqual(outcome["missed_match_by_none_tier"], 0)
        self.assertEqual(outcome["correctly_unmatched_by_none_tier"], 44)

    def test_strong_auto_post_tier_not_in_sample(self):
        labels, snapshot = self.load_case()
        metrics = score(snapshot, labels)
        self.assertEqual(metrics["strong_tier_human_reviewed"], 0)
        self.assertIsNone(metrics["sampling"]["population_precision"])
        self.assertIsNone(metrics["sampling"]["population_recall"])
        self.assertIn("stratified", metrics["sampling"]["limitation"])

    def test_positive_ids_are_hcjb_and_vinny_only(self):
        labels, snapshot = self.load_case()
        actual = {k for k, v in labels.items() if v["review_label"] == "match"}
        self.assertEqual(actual, {"jxoHvBlRABA", "E2tRH0B6Ow8"})

    def test_duplicate_label_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "labels.csv"
            path.write_text("video_id,review_label,reviewer,notes\n"
                            "jxoHvBlRABA,match,human,\n"
                            "jxoHvBlRABA,match,human,\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                read_human_labels(path)

    def test_nonhuman_or_uncertain_label_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "labels.csv"
            path.write_text("video_id,review_label,reviewer,notes\n"
                            "jxoHvBlRABA,match,ai,\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "human"):
                read_human_labels(path)
            path.write_text("video_id,review_label,reviewer,notes\n"
                            "jxoHvBlRABA,uncertain,human,\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unverified"):
                read_human_labels(path)

    def test_new_label_outside_frozen_scan_rejected(self):
        labels, snap = self.load_case()
        labels["abcdefghXYZ"] = {"video_id":"abcdefghXYZ",
                                "review_label":"match","reviewer":"human","notes":""}
        with self.assertRaisesRegex(ValueError, "disagree"):
            score(snap, labels)

    def test_altering_audited_classifier_tier_rejected(self):
        labels, snap = self.load_case()
        snap["rows"][0]["tier"] = "automatic_alert"
        with self.assertRaisesRegex(ValueError, "Invalid classifier tier"):
            score(snap, labels)


if __name__ == "__main__":
    unittest.main()
