"""Offline-delivery pipeline guards for commentary-style genuine reaction titles."""
import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("YOUTUBE_API_KEY", "offline-test-key")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/test")
from new_channel_reaction_evidence import clear_reaction_title
from autoreactions import is_safe_reaction_candidate, recent_enough_for_first_expanded_match


class ContextualDeliveryTests(unittest.TestCase):
    def test_terrence_jr_metadata_is_accepted_without_reaction_in_title(self):
        item = {
            "video_id": "7F7-Lma-yu4",
            "channel_id": "UCju_HuzKeD3oBO_EYLvPNmA",
            "channel": "TerrenceJr",
            "title": "HER SOLO WAS CRAZY !! | HIGHWAY STAR - DEEP PURPLE BY MISSIONED SOULS",
            "description": "Today we are checking out this amazing Family Rock band "
                           "MISSIONED SOULS playing HIGHWAY STAR BY DEEP PURPLE",
        }
        self.assertTrue(clear_reaction_title(item))
        self.assertTrue(is_safe_reaction_candidate(item))

    def test_explicit_compound_reaction_hashtag_is_recognized(self):
        # Metadata taken from an audit candidate, but no replay of old uploads.
        sample = {
            "title": "MISSIONED SOULS, HATE MYSELF #MissionedSouls "
                     "#IHateMyselfForLovingYou #MissionedSoulsReaction",
            "channel": "RJS_Reactions",
            "description": "",
        }
        self.assertTrue(clear_reaction_title(sample))
        self.assertTrue(is_safe_reaction_candidate(sample))
        sample["published_at"] = "2026-10-21T00:00:00Z"
        self.assertFalse(recent_enough_for_first_expanded_match(
            sample, now=datetime(2026, 10, 23, 4, 45, tzinfo=timezone.utc)
        ))

    def test_promotional_check_out_and_self_performed_cover_are_rejected(self):
        for desc in (
            "Check out my channel! Missioned Souls performs Highway Star.",
            "Our original performance of Missioned Souls' Highway Star. "
            "Today we are checking out Missioned Souls.",
        ):
            with self.subTest(desc=desc):
                self.assertFalse(clear_reaction_title({
                    "title": "HIGHWAY STAR - DEEP PURPLE BY MISSIONED SOULS",
                    "description": desc, "channel": "Example channel",
                }))

    def test_newly_recognized_format_uses_24h_initial_window(self):
        now = datetime(2026, 10, 23, 4, 45, tzinfo=timezone.utc)
        item = {
            "title": "HER SOLO WAS CRAZY !! | HIGHWAY STAR - DEEP PURPLE BY MISSIONED SOULS",
            "channel": "TerrenceJr",
            "description": "Today we are checking out MISSIONED SOULS",
            "published_at": (now - timedelta(hours=25)).isoformat(),
        }
        self.assertFalse(recent_enough_for_first_expanded_match(item, now=now))


if __name__ == "__main__":
    unittest.main()
