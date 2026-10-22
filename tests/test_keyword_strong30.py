"""Regression of 30 human strong-tier reviews and adversarial note handling."""
import json
import tempfile
import unittest
from pathlib import Path
from scripts.score_keyword_strong30 import LABELS, SELECTION, load_labels, evaluate


class Strong30ReviewTests(unittest.TestCase):
    def case(self):
        return json.loads(SELECTION.read_text(encoding="utf-8")), load_labels(LABELS)

    def test_exact_ids_and_order(self):
        frozen, rows = self.case()
        self.assertEqual(len(rows), 30)
        self.assertEqual([r["video_id"] for r in rows], frozen["video_ids_in_review_order"])

    def test_preserves_submitted_raw_labels(self):
        frozen, rows = self.case()
        r=evaluate(frozen, rows)
        self.assertEqual(r["raw_marked_match"], 29)
        self.assertEqual(r["raw_marked_not_match"], 1)
        self.assertEqual(r["reviewed"], 30)

    def test_naces_tribute_conflict_remains_disputed(self):
        frozen, rows = self.case()
        r=evaluate(frozen, rows)
        self.assertEqual(r["disputed"], 1)
        naces=next(x for x in r["per_video"] if x["video_id"]=="fqTvzUxJT1k")
        self.assertEqual(naces["raw_user_label"], "match")
        self.assertEqual(naces["effective_status"], "disputed")
        self.assertTrue(naces["needs_confirmation"])
        self.assertIn("Not an actual reaction", naces["raw_user_note"])

    def test_paper_dove_exclusion_human_confirmed(self):
        frozen, rows=self.case()
        r=evaluate(frozen, rows)
        pd=next(x for x in r["per_video"] if x["video_id"]=="r5TVQYSmZcY")
        self.assertEqual(pd["effective_status"], "not_match")
        self.assertIn("Paper Dove",pd["raw_user_note"])
        self.assertEqual(r["confirmed_not_match"],1)

    def test_only_29_consistent_and_no_population_precision(self):
        frozen, rows=self.case()
        r=evaluate(frozen, rows)
        self.assertEqual(r["confirmed_match"], 28)
        self.assertEqual(r["consistent_labels"], 29)
        self.assertEqual(r["confirmed_match_fraction_of_consistent_sample"],round(28/29,4))
        self.assertIsNone(r["population_precision"])
        self.assertIsNone(r["population_recall"])

    def test_no_extrapolation_from_risk_enriched_sample(self):
        frozen, rows=self.case()
        r=evaluate(frozen, rows)
        self.assertIn("risk",r["sample_selection"])
        self.assertIn("pre-confirmed",r["limitations"][0])

    def test_unknown_id_or_missing_label_fails_closed(self):
        frozen, rows=self.case()
        with self.assertRaisesRegex(ValueError, "do not exactly match"):
            evaluate(frozen,rows[:-1])
        rows[0]["video_id"]="abcdefghXYZ"
        with self.assertRaisesRegex(ValueError, "do not exactly match"):
            evaluate(frozen,rows)

    def test_duplicate_or_nonhuman_review_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"x.csv"
            p.write_text("video_id,review_label,reviewer,notes\n"
                         "fqTvzUxJT1k,match,human,\n"
                         "fqTvzUxJT1k,match,human,\n",encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "repeated"):
                load_labels(p)
            p.write_text("video_id,review_label,reviewer,notes\n"
                         "fqTvzUxJT1k,match,ai,\n",encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unverified"):
                load_labels(p)

    def test_no_crash_with_valid_csv_newline_in_notes(self):
        rows=load_labels(LABELS)
        q=next(x for x in rows if x["video_id"]=="fqTvzUxJT1k")
        self.assertIn("\n",q["notes"])


if __name__ == "__main__":
    unittest.main()
