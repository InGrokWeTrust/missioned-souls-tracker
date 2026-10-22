"""Tests for blind keyword false-negative audit and HTML review export."""
import unittest
from scripts.keyword_miss_audit import audit, independent_flags, make_review_html


class KeywordMissAuditTests(unittest.TestCase):
    def make_row(self,idx,channel,tier="none",title="Unrelated music discussion",
                 description="",tags=None):
        return {
            "video_id":f"abcdefghi{idx:02d}",
            "channel":channel,"channel_id":channel,
            "title":title,"tier":tier,
            "description_excerpt":description,
            "description_length":len(description),
            "tags":tags or [],"evidence":[],"reasons":[],
            "url":"https://www.youtube.com/watch?v="+f"abcdefghi{idx:02d}",
        }

    def test_description_name_outside_title_flagged_not_confirmed(self):
        row=self.make_row(0,"channel1",description="Video about Missioned Souls")
        self.assertIn("band_mentioned_anywhere",independent_flags(row))
        result=audit({"all_scanned":[row],"created_at":"2026-10-22T04:00:00Z"})
        self.assertEqual(result["flagged_no_match_total"],1)
        self.assertEqual(result["selected_possible_miss"],1)
        self.assertIsNone(result["samples"][0]["human_ground_truth"])

    def test_absence_of_artist_clues_produces_unflagged_random_control(self):
        row=self.make_row(1,"channel1")
        result=audit({"all_scanned":[row]})
        self.assertEqual(result["flagged_no_match_total"],0)
        self.assertEqual(result["selected_random_unmatched"],1)
        self.assertEqual(result["samples"][0]["sample_type"],"random_unmatched")

    def test_review_candidates_retained_without_ground_truth(self):
        row=self.make_row(2,"channel1",tier="review",title="Total Ecplipse")
        result=audit({"all_scanned":[row]})
        self.assertEqual(result["selected_review"],1)
        self.assertEqual(result["samples"][0]["human_ground_truth"],None)

    def test_channel_balancing_no_channel_over_three_nonmatch_samples(self):
        rows=[self.make_row(i,"channel1",title="Unrelated "+str(i))
              for i in range(10)]
        rows += [self.make_row(i,"channel2",title="New unrelated "+str(i))
                 for i in range(10,20)]
        result=audit({"all_scanned":rows},random_limit=20)
        self.assertEqual(result["selected_random_unmatched"],6)
        self.assertEqual(len(result["samples"]),6)

    def test_independent_flags_generic_song_not_proof(self):
        row=self.make_row(3,"channel1",title="Crazy Train guitar solo")
        self.assertEqual(independent_flags(row),[])

    def test_generic_youtube_links_are_not_artist_evidence(self):
        row=self.make_row(20,"channel1",description=
                          "Original video: https://youtu.be/abcdefghXYZ "
                          "or https://www.youtube.com/watch?v=abcdefghXYZ")
        self.assertEqual(independent_flags(row),[])
        result=audit({"all_scanned":[row]})
        self.assertEqual(result["selected_random_unmatched"],1)
        self.assertEqual(result["flagged_no_match_total"],0)

    def test_official_artist_channel_links_are_flagged(self):
        row=self.make_row(21,"channel2",description=
                          "Original artist: https://www.youtube.com/@missionedsouls")
        self.assertIn("original_artist_reference",independent_flags(row))
        self.assertEqual(audit({"all_scanned":[row]})["flagged_no_match_total"],1)

    def test_long_description_is_diagnostic_not_suspicious(self):
        row=self.make_row(22,"channel3",description="Generic text "*300)
        row["description_excerpt"]=row["description_excerpt"][:220]
        self.assertEqual(independent_flags(row),[])
        result=audit({"all_scanned":[row]})
        self.assertEqual(result["truncated_description_count"],1)
        self.assertEqual(result["flagged_no_match_total"],0)

    def test_review_html_escapes_untrusted_title(self):
        row=self.make_row(4,"channel1",title="</script><script>alert('BAD')</script>")
        page=make_review_html([{**row,"sample_type":"random_unmatched","audit_flags":[]}])
        self.assertNotIn("</script><script>alert",page)
        self.assertIn("\\u003c",page)
        self.assertIn("keyword-manual-labels.csv",page)
        self.assertIn("Open and verify this video",page)

    def test_review_html_does_not_auto_send_or_assign_labels(self):
        row=self.make_row(5,"channel2")
        page=make_review_html([{**row,"sample_type":"random_unmatched","audit_flags":[]}])
        self.assertIn("UNSURE",page)
        self.assertIn("match",page)
        self.assertNotIn("fetch(",page)
        self.assertNotIn("discord.com/api",page)


if __name__=="__main__":
    unittest.main()
