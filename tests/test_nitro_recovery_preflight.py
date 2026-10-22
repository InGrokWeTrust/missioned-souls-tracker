"""Recovery status checks use saved state only, never an API or Discord."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.nitro_recovery_preflight import check_state
from reaction_state import StateError

VID="hf03Voz2QBM"
CID="UC-lB_sG0uviaY9pD07kiwjg"


def state(seen=None,pending=None):
    return {
        "version":2,"last_published_at":"2026-10-19T00:00:00Z",
        "seen_video_ids":seen or [],"known_channel_ids":[CID],
        "pending":pending or [],
    }


def pending_row():
    return {"video_id":VID,"channel_id":CID,"title":"Nitro Neisha TEARS It Up On This Classic!",
            "url":"https://youtu.be/"+VID,"published_at":"2026-10-20T00:00:00Z"}


class NitroRecoveryPreflightTests(unittest.TestCase):
    def run_case(self,payload,video_id=VID):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"last_run.json"
            p.write_text(json.dumps(payload))
            before=p.read_bytes()
            result=check_state(str(p),video_id=video_id)
            self.assertEqual(before,p.read_bytes())
            return result

    def test_seen_not_reposted(self):
        r=self.run_case(state(seen=[VID]))
        self.assertEqual(r["status"],"already_seen_do_not_repost")
        self.assertFalse(r["discord_posted"])

    def test_pending_not_queued_twice(self):
        r=self.run_case(state(pending=[pending_row()]))
        self.assertEqual(r["status"],"already_pending_do_not_duplicate")
        self.assertFalse(r["state_changed"])

    def test_missing_id_needs_explicit_one_video_recovery(self):
        r=self.run_case(state())
        self.assertEqual(r["status"],"not_in_bookmark_controlled_recovery_required")
        self.assertIn("five latest uploads",r["limits"])
        self.assertFalse(r["discord_posted"])

    def test_unknown_video_rejected(self):
        with self.assertRaisesRegex(ValueError,"not been individually verified"):
            self.run_case(state(),video_id="notconfirmed")

    def test_legacy_bookmark_needs_migration_and_cannot_send(self):
        r=self.run_case({"last_published_at":"2026-10-19T00:00:00Z"})
        self.assertEqual(r["status"],"legacy_state_requires_migration")
        self.assertFalse(r["discord_posted"])

    def test_missing_bookmark_raises_preserving_safety(self):
        with self.assertRaises(StateError):
            check_state("/no/such/last_run.json")


if __name__=="__main__":
    unittest.main()
