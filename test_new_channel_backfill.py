"""Offline discovery-to-notification safety tests with no YouTube or Discord."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import reaction_state as state
import autoreactions as reactions
from new_channel_reaction_evidence import clear_reaction_title
from tests.test_delivery import blank_state, video


NOW = datetime(2026, 10, 22, 20, tzinfo=timezone.utc)
NEW_CHANNEL = "UCTR_PSXLloLzLMBVxnt_kLQ"
ADDED = "2026-10-22T19:01:20Z"


def case(id, title, published="2026-10-22T18:10:50Z"):
    item = video(id, channel=NEW_CHANNEL, published=published)
    item["title"] = title
    return item


class BackfillTests(unittest.TestCase):
    def test_linked_studio_cover_is_not_automatically_reaction(self):
        linked = case(1, "TOTAL ECLIPSE OF THE HEART by Bonnie Tyler | Missioned Souls | Family Band Studio Cover")
        self.assertFalse(clear_reaction_title(linked))
        result = blank_state()
        state.plan_notifications(result, [linked], {"UC1", NEW_CHANNEL}, now=NOW,
                                 channel_added_at={NEW_CHANNEL: ADDED})
        self.assertEqual(result["pending"], [])
        self.assertIn(linked["video_id"], result["seen_video_ids"])

    def test_new_channel_recent_explicit_reaction_delivered_only_once(self):
        first = case(1, "First Time Hearing Missioned Souls - Total Eclipse Reaction")
        with tempfile.TemporaryDirectory() as directory:
            bookmark = Path(directory) / "last_run.json"
            bookmark.write_text(json.dumps(blank_state()), encoding="utf-8")
            sent = []
            def fake_send(items, **kwargs):
                for item in items:
                    kwargs["on_success"](item)
                sent.extend(items)
                return list(items)
            with patch.object(reactions, "LAST_RUN_FILE", str(bookmark)), \
                 patch.object(reactions, "LIVE_LAST_RUN_FILE", str(Path(directory) / "no-live.json")), \
                 patch.object(reactions, "send_to_discord", side_effect=fake_send):
                delivered = reactions.run_tracker(
                    [first], {"UC1", NEW_CHANNEL}, run_start=NOW,
                    channel_added_at={NEW_CHANNEL: ADDED})
                repeated = reactions.run_tracker(
                    [first], {"UC1", NEW_CHANNEL}, run_start=NOW,
                    channel_added_at={NEW_CHANNEL: ADDED})
            self.assertEqual([item["video_id"] for item in delivered], [first["video_id"]])
            self.assertEqual(repeated, [])
            self.assertEqual([item["video_id"] for item in sent], [first["video_id"]])
            latest = state.load_state(bookmark)
            self.assertEqual(latest["pending"], [])
            self.assertIn(first["video_id"], latest["seen_video_ids"])

    def test_first_scan_backfill_is_limited_to_two_per_channel(self):
        videos = [case(i, f"Missioned Souls reaction {i}") for i in range(1, 6)]
        result = blank_state()
        state.plan_notifications(result, videos, {"UC1", NEW_CHANNEL}, now=NOW,
                                 channel_added_at={NEW_CHANNEL: ADDED})
        self.assertEqual([v["video_id"] for v in result["pending"]], ["id-1", "id-2"])
        self.assertEqual(len(result["seen_video_ids"]), 3)

    def test_old_channel_discovery_and_old_video_stay_seeded(self):
        old = case(1, "Missioned Souls reaction", "2026-10-18T18:00:00Z")
        result = blank_state()
        state.plan_notifications(result, [old], {"UC1", NEW_CHANNEL}, now=NOW,
                                 channel_added_at={NEW_CHANNEL: ADDED})
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["seen_video_ids"], ["id-1"])

    def test_delayed_first_scan_after_three_hours_does_not_backfill(self):
        item = case(1, "Missioned Souls reaction")
        result = blank_state()
        late = datetime(2026, 10, 23, 2, tzinfo=timezone.utc)
        state.plan_notifications(result, [item], {"UC1", NEW_CHANNEL}, now=late,
                                 channel_added_at={NEW_CHANNEL: ADDED})
        self.assertEqual(result["pending"], [])
        self.assertIn(item["video_id"], result["seen_video_ids"])

    def test_missing_registered_at_never_backfills(self):
        item = case(1, "Missioned Souls reaction")
        result = blank_state()
        state.plan_notifications(result, [item], {"UC1", NEW_CHANNEL}, now=NOW,
                                 channel_added_at={})
        self.assertEqual(result["pending"], [])
        self.assertIn(item["video_id"], result["seen_video_ids"])

    def test_already_delivered_or_seeded_ids_not_requeued(self):
        item = case(1, "Missioned Souls reaction")
        result = blank_state()
        result["seen_video_ids"] = [item["video_id"]]
        state.plan_notifications(result, [item], {"UC1", NEW_CHANNEL}, now=NOW,
                                 channel_added_at={NEW_CHANNEL: ADDED})
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["seen_video_ids"], ["id-1"])

    def test_bootstrap_and_migration_do_not_backfill(self):
        item = case(1, "Missioned Souls reaction")
        for special in (
            state._empty_state(bootstrap=True),
            state._empty_state(legacy_cutoff="2026-10-22T20:00:00Z"),
        ):
            with self.subTest(special=special):
                state.plan_notifications(special, [item], {NEW_CHANNEL}, now=NOW,
                                         channel_added_at={NEW_CHANNEL: ADDED})
                self.assertEqual(special["pending"], [])

    def test_excludes_reuploads_promotions_and_paper_dove(self):
        for title in (
            "Missioned Souls reaction archive repost",
            "Missioned Souls Reaction Highlights",
            "Missioned Souls watch party reaction",
            "#MissionedSouls Paper Dove reaction",
            "Missioned Souls studio cover",
        ):
            with self.subTest(title=title):
                self.assertFalse(clear_reaction_title({"title": title}))
        for title in ("Missioned Souls reaction", "Reacts To Missioned Souls",
                      "First time listening to Missioned Souls"):
            self.assertTrue(clear_reaction_title({"title": title}))


if __name__ == "__main__":
    unittest.main()
