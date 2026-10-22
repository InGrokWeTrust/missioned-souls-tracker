"""Offline guardrails for experimental vision, candidate harvesting and cost limits."""
import json
import os
import tempfile
from pathlib import Path
from io import BytesIO
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-only")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/unused")
from vision_evaluator import (analyze, build_request, download_thumbnail,
                              metadata_evidence, parse_response, score_labelled,
                              thumbnail_url)
from scripts.collect_vision_candidates import parse_feed, collect, saved_state_candidates, create_review_csv
from scripts.run_vision_evaluation import evaluate, cost_from_usage


def example():
    return {
        "video_id": "hf03Voz2QBM",
        "channel": "Taylor John Hardin Reactions",
        "title": "Nitro Neisha TEARS It Up On This Classic!",
        "description": "",
        "tags": [],
        "ground_truth": None,
        "reviewed_by": None,
    }


def mock_response(label="match"):
    return {
        "status": "completed",
        "usage": {"input_tokens": 1000, "output_tokens": 100},
        "output": [{"type": "message", "content": [{
            "type": "output_text", "text": json.dumps({
                "label": label, "evidence": "Visible MISSIONED SOULS lettering",
                "reaction_evidence": "The reactor is reacting", "reason": "Specific artist and reaction clues"
            })
        }]}],
    }


class VisionOfflineTests(unittest.TestCase):
    def test_title_free_neisha_is_not_claimed_by_metadata(self):
        self.assertFalse(metadata_evidence(example())["strong_metadata"])

    def test_relevant_description_detects_explicit_artist(self):
        item = example()
        item["description"] = "My first reaction to Missioned Souls! Incredible guitar solo."
        self.assertTrue(metadata_evidence(item)["strong_metadata"])

    def test_description_boilerplate_without_reaction_context_not_strong(self):
        item = example()
        item["title"] = "A random gaming video"
        item["description"] = "I've covered a lot of bands, including Missioned Souls."
        self.assertFalse(metadata_evidence(item)["strong_metadata"])

    def test_unrelated_song_title_does_not_prove_artist(self):
        item = example()
        item["title"] = "I Hate Myself For Loving You - first time reaction"
        self.assertFalse(metadata_evidence(item)["strong_metadata"])

    def test_exact_artist_tag_plus_reaction_context(self):
        item = example()
        item["title"] = "First time reaction to talented guitarist"
        item["tags"] = ["Missioned Souls"]
        self.assertTrue(metadata_evidence(item)["strong_metadata"])

    def test_trusted_thumbnail_url_only(self):
        self.assertTrue(thumbnail_url("hf03Voz2QBM").startswith("https://i.ytimg.com/"))
        with self.assertRaises(ValueError):
            thumbnail_url("unsafe-id")

    def test_thumbnail_fetch_rejects_untrusted_host(self):
        with self.assertRaisesRegex(ValueError, "Untrusted"):
            download_thumbnail("https://internal.example/foo", getter=lambda *a, **kw: None)

    def test_thumbnail_payload_and_no_storage(self):
        request = build_request(example(), b"fake-jpeg", model="gpt-6-luna")
        self.assertIs(request["store"], False)
        self.assertEqual(request["input"][0]["content"][1]["type"], "input_image")
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertIn("data:image/jpeg;base64,", request["input"][0]["content"][1]["image_url"])

    def test_response_must_be_completed_and_valid(self):
        self.assertEqual(parse_response(mock_response())["label"], "match")
        with self.assertRaises(ValueError):
            parse_response({"status": "incomplete", "output": []})
        with self.assertRaises(ValueError):
            parse_response({"status": "completed", "output": []})

    def test_model_call_uses_mocks_and_never_posts_discord(self):
        def getter(url, **kwargs):
            return SimpleNamespace(status_code=200, content=b"thumbnail",
                                   headers={"Content-Type": "image/jpeg"},
                                   raise_for_status=lambda: None)
        def post(url, headers, json, timeout):
            self.assertIn("/responses", url)
            self.assertEqual(headers["Authorization"], "Bearer fake-test-key")
            self.assertFalse(json["store"])
            return SimpleNamespace(json=lambda: mock_response(),
                                   raise_for_status=lambda: None)
        result = analyze(example(), api_key="fake-test-key", getter=getter, post=post)
        self.assertEqual(result["label"], "match")
        self.assertEqual(result["usage"]["input_tokens"], 1000)

    def test_human_labels_only_are_scored(self):
        data = [
            {"ground_truth": "match", "prediction": "match", "reviewed_by": "human"},
            {"ground_truth": "not_match", "prediction": "match", "reviewed_by": "human"},
            {"ground_truth": "match", "prediction": "uncertain", "reviewed_by": "human"},
            {"ground_truth": "not_match", "prediction": "not_match", "reviewed_by": "human"},
            {"ground_truth": "match", "prediction": "match", "reviewed_by": None},
        ]
        r = score_labelled(data)
        self.assertEqual(r["labelled"], 4)
        self.assertEqual(r["true_positive"], 1)
        self.assertEqual(r["false_positive"], 1)
        self.assertEqual(r["missed_positive_including_uncertain"], 1)
        self.assertEqual(r["precision"], .5)
        self.assertEqual(r["recall"], .5)
        self.assertEqual(r["coverage"], .75)

    def test_undefined_denominator_returns_null_metrics(self):
        m = score_labelled([])
        self.assertEqual(m["labelled"], 0)
        self.assertIsNone(m["precision"])
        self.assertIsNone(m["recall"])

    def test_rss_parser_yields_unlabelled_candidates(self):
        feed = ('<feed xmlns="http://www.w3.org/2005/Atom" '
                'xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry>'
                '<yt:videoId>hf03Voz2QBM</yt:videoId><title>Neisha reaction</title>'
                '<published>2026-10-22T03:00:00Z</published></entry></feed>')
        rows = parse_feed(feed, "UCexample", "A reactor")
        self.assertEqual(rows[0]["video_id"], "hf03Voz2QBM")
        self.assertIsNone(rows[0]["ground_truth"])

    def test_round_robin_balances_channels(self):
        channels = {f"UC{i:022d}": {"title": f"React channel {i}"} for i in range(4)}
        def fake_fetch(entry):
            cid, name = entry
            n = int(name.rsplit(" ", 1)[-1])
            return ([{"video_id": f"{n:02d}{i:09d}", "channel_id": cid} for i in range(3)], None)
        rows, attempted, errors = collect(channels, getter=fake_fetch, target=9, max_channels=4)
        self.assertEqual(attempted, 4)
        self.assertEqual(len(rows), 9)
        self.assertEqual(len(set(x["channel_id"] for x in rows)), 4)
        self.assertFalse(errors)

    def test_unlabelled_state_history_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last_run.json"
            path.write_text(json.dumps({
                "seen_video_ids": ["hf03Voz2QBM", "Lr4gUjZVjT0", "bad id"],
                "version": 2
            }), encoding="utf-8")
            rows = saved_state_candidates(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["video_id"], "Lr4gUjZVjT0")
            self.assertIsNone(rows[0]["ground_truth"])
            self.assertEqual(rows[0]["source"], "read-only production video-ID history")

    def test_manual_review_csv_has_blank_truth_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.csv"
            rows = [{"video_id":"hf03Voz2QBM","thumbnail_url":
                     "https://i.ytimg.com/vi/hf03Voz2QBM/hqdefault.jpg",
                     "source":"test"}]
            create_review_csv(rows, path=path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("ground_truth,reviewed_by,review_notes", text)
            self.assertIn("youtube.com/watch?v=hf03Voz2QBM", text)

    def test_cost_guard_and_usage_math(self):
        self.assertAlmostEqual(cost_from_usage(
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
            "gpt-6-luna"), 0.60)
        with self.assertRaises(ValueError):
            cost_from_usage({}, "gpt-6-luna")

    def test_paid_runner_enforces_call_cap_with_stubbed_model(self):
        samples = [dict(example(), video_id=f"abcdefgh{i:02d}") for i in range(5)]
        def classifier(sample, api_key, model):
            return {
                "label": "uncertain",
                "vision": {"reason": "Insufficient evidence", "evidence": ""},
                "metadata": {},
                "usage": {"input_tokens": 500, "output_tokens": 100},
            }
        r = evaluate({"samples": samples}, api_key="fake", model="gpt-6-luna",
                     max_calls=2, budget_usd=.50, classifier=classifier)
        self.assertEqual(r["executed"], 2)
        self.assertEqual(r["metrics"]["labelled"], 0)


if __name__ == "__main__":
    unittest.main()
