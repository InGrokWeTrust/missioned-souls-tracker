"""No-network safety tests for reaction-only delivery and non-delivery history."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autoreactions as tracker
import notification_history as history
from new_channel_reaction_evidence import is_strong_reaction_candidate
from tests.test_delivery import NOW, blank_state, video
from reaction_state import load_state

MRJ="UCTR_PSXLloLzLMBVxnt_kLQ"


def candidate(title, *, description="", id=1, channel="UC1"):
    v=video(id, channel=channel)
    v["title"]=title
    v["description"]=description
    return v


class ReactionQualityProductionTests(unittest.TestCase):
    def test_public_mrj_title_and_reaction_hashtags(self):
        v=candidate("TOTAL ECLIPSE OF THE HEART by Bonnie Tyler | Missioned Souls | Family Band Studio Cover",
                    description="#missionedsouls #reaction #reactionvideo #musicianreacts",
                    channel=MRJ)
        self.assertTrue(is_strong_reaction_candidate(v))
        self.assertTrue(tracker.is_safe_reaction_candidate(v))

    def test_vlog_and_travel_teaser_never_sent_even_when_description_mentions_reacting(self):
        v=candidate("SURPRISE! I Flew to the Philippines to Meet Missioned Souls!",
                    description="After months of watching and reacting to Missioned Souls...")
        self.assertFalse(tracker.is_safe_reaction_candidate(v))
        with patch.object(tracker, "post_webhook") as post:
            self.assertEqual(tracker.send_to_discord([v]), [])
            post.assert_not_called()

    def test_band_mention_alone_is_not_reaction(self):
        for title in ("Missioned Souls studio cover",
                      "Missioned Souls tour news", "Missioned Souls fan meet",
                      "Missioned Souls discussion", "Missioned Souls live trailer",
                      "Missioned Souls original concert performance",
                      "#MissionedSouls Paper Dove cover",
                      "Missioned Souls reaction archive"):
            with self.subTest(title=title):
                self.assertFalse(tracker.is_safe_reaction_candidate(candidate(title)))

    def test_reaction_text_and_qualified_metadata_are_allowed(self):
        for title, description in (
                ("MISSIONED SOULS - BLACK VELVET REACTION", ""),
                ("Hillbilly Reactions Missioned Souls Black Velvet cover", ""),
                ("Missioned Souls - COVER - Black Velvet", "#reaction #firstreaction"),
                ("First Time Hearing Missioned Souls - Hope", ""),
        ):
            with self.subTest(title=title):
                self.assertTrue(tracker.is_safe_reaction_candidate(
                    candidate(title, description=description)))

    def test_old_pending_vlog_is_held_not_posted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"last_run.json"
            state=blank_state()
            state["pending"]=[candidate("SURPRISE! I Flew to the Philippines to Meet Missioned Souls!")]
            p.write_text(json.dumps(state))
            with patch.object(tracker, "LAST_RUN_FILE",str(p)), \
                 patch.object(tracker,"LIVE_LAST_RUN_FILE",str(Path(d)/"missing-live.json")), \
                 patch.object(tracker,"reconfirm_older_pending_vods",return_value=set()), \
                 patch.object(tracker,"send_to_discord") as send:
                result=tracker.run_tracker([],{"UC1"},run_start=NOW)
                send.assert_not_called()
            self.assertEqual(result,[])
            self.assertEqual(len(load_state(p)["pending"]),1)

    def test_after_success_only_confirmed_id_passed_to_durable_history(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"last_run.json"
            state=blank_state()
            state["seen_video_ids"]=["already-seeded-never-delivered"]
            p.write_text(json.dumps(state))
            item=video(1)
            def simulated_discord(items,**kwargs):
                for v in items:kwargs["on_success"](v)
                return items
            with patch.object(tracker,"LAST_RUN_FILE",str(p)), \
                 patch.object(tracker,"LIVE_LAST_RUN_FILE",str(Path(d)/"missing-live.json")), \
                 patch.object(tracker,"send_to_discord",side_effect=simulated_discord), \
                 patch.object(tracker,"save_durable_ids") as save:
                result=tracker.run_tracker([item],{"UC1"},run_start=NOW)
            self.assertEqual(len(result),1)
            save.assert_called_once_with("reactions",[item["video_id"]])
            self.assertIn("already-seeded-never-delivered",load_state(p)["seen_video_ids"])

    def test_hydration_does_not_add_local_seeded_id_to_sent_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"last_run.json"
            state=blank_state()
            state["seen_video_ids"]=["locally-seeded"]
            p.write_text(json.dumps(state))
            with patch.object(history,"DRY_RUN",False), \
                 patch.object(history,"_read_remote",return_value=(["confirmed"],"sha")), \
                 patch.object(history,"save_ids") as save:
                merged=history.hydrate("reactions",p)
                save.assert_not_called()
            self.assertEqual(merged,["confirmed","locally-seeded"])
            self.assertEqual(load_state(p)["seen_video_ids"],["confirmed","locally-seeded"])


if __name__=="__main__":
    unittest.main()
