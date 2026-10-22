"""Prevent next independent reactor-history review set leaking old labels."""
import json
import unittest
from pathlib import Path

from scripts.evaluate_reactor_history import (
    REGISTRY, MAP, STRONG, FIVE, EARLIER, _csv, build_cases, assess_all,
)

PENDING=Path(__file__).resolve().parents[1]/"data"/"reactor_history_next7_pending_review_20261022.json"


class NextReactorHistoryReviewTests(unittest.TestCase):
    def sample(self):
        return json.loads(PENDING.read_text(encoding="utf-8"))

    def test_exactly_seven_different_unlabelled_videos(self):
        items=self.sample()["items"]
        self.assertEqual(len(items),7)
        self.assertEqual(len({v["video_id"] for v in items}),7)
        self.assertTrue(all(v["review_label"] is None for v in items))
        self.assertTrue(all(v["url"]=="https://www.youtube.com/watch?v="+v["video_id"]
                            for v in items))

    def test_none_duplicate_prior_76_human_reviews(self):
        cases=build_cases(json.loads(STRONG.read_text(encoding="utf-8")),
                          _csv(FIVE),_csv(EARLIER))
        prior_ids=set(cases)
        prior_ids.update(v["video_id"] for v in _csv(EARLIER))
        self.assertFalse(prior_ids.intersection(v["video_id"] for v in self.sample()["items"]))

    def test_all_channels_are_in_registry_and_confirmed_reactors(self):
        reg=json.loads(REGISTRY.read_text(encoding="utf-8"))["channels"]
        mapping=json.loads(MAP.read_text(encoding="utf-8"))
        cases=build_cases(json.loads(STRONG.read_text(encoding="utf-8")),
                          _csv(FIVE),_csv(EARLIER))
        report=assess_all(reg,mapping,cases)
        byid={x["channel_id"]:x for x in report["channels"]}
        items=self.sample()["items"]
        self.assertEqual(len({x["channel_id"] for x in items}),3)
        self.assertEqual({x["channel"] for x in items},{
            "HCJB Music Reaction","Reactions with Vinny","Global take"})
        for item in items:
            with self.subTest(video=item["video_id"]):
                self.assertIn(item["channel_id"],reg)
                self.assertEqual(item["channel"],byid[item["channel_id"]]["name"])
                self.assertEqual(byid[item["channel_id"]]["confirmed_reactions"],1)

    def test_dedicated_song_is_unlabelled_harder_negative(self):
        item=next(x for x in self.sample()["items"] if x["video_id"]=="pbgQdCCaPo8")
        self.assertIn("Dedicated Song",item["title"])
        self.assertIsNone(item["review_label"])

    def test_no_autopost_or_global_channel_reliability_claim(self):
        sample=self.sample()
        self.assertTrue(sample["no_labels_assigned"])
        self.assertIn("cannot establish global",sample["selection_limits"])
        self.assertNotIn("approved_for_discord",sample)


if __name__=="__main__":
    unittest.main()
