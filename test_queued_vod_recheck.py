"""Offline regression tests for queued reactions falling outside newest uploads."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import autoreactions as tracker
from reaction_state import StateError, load_state
from tests.test_delivery import NOW, blank_state, video


def youtube_item(queued, status="none", *, details=None, channel=None,
                 title=None, duration="PT8M", privacy="public"):
    return {
        "id": queued["video_id"],
        "snippet": {
            "channelId": channel or queued["channel_id"],
            "title": title or queued["title"],
            "liveBroadcastContent": status,
        },
        "contentDetails": {"duration": duration},
        "status": {"privacyStatus": privacy},
        **({"liveStreamingDetails": details} if details is not None else {}),
    }


class QueuedVODRecheckTests(unittest.TestCase):
    def test_published_older_queued_video_delivered_after_disappearing_from_recent_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            bookmark = Path(directory) / "last_run.json"
            queued = video(1)
            initial = blank_state()
            initial["pending"] = [queued]
            bookmark.write_text(json.dumps(initial), encoding="utf-8")
            yt = MagicMock()
            yt.videos.return_value.list.return_value.execute.return_value = {
                "items": [youtube_item(queued)]}
            delivered = []
            def fake_send(items, **kwargs):
                for item in items:
                    kwargs["on_success"](item)
                delivered.extend(items)
                return items
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), \
                 patch.object(tracker, "LIVE_LAST_RUN_FILE", str(Path(directory) / "no-live.json")), \
                 patch.object(tracker, "youtube", yt), \
                 patch.object(tracker, "send_to_discord", side_effect=fake_send):
                sent = tracker.run_tracker([], {"UC1"}, run_start=NOW)
            self.assertEqual([x["video_id"] for x in sent], [queued["video_id"]])
            self.assertEqual(len(delivered), 1)
            self.assertEqual(load_state(bookmark)["pending"], [])
            self.assertIn(queued["video_id"], load_state(bookmark)["seen_video_ids"])
            self.assertEqual(yt.videos.return_value.list.call_args.kwargs["id"],
                             queued["video_id"])

    def test_upcoming_live_private_and_wrong_channel_remain_pending(self):
        queue = [video(i) for i in range(1, 5)]
        variants = [
            youtube_item(queue[0], status="upcoming"),
            youtube_item(queue[1], status="live"),
            youtube_item(queue[2], privacy="private"),
            youtube_item(queue[3], channel="different-channel"),
        ]
        client = MagicMock()
        client.videos.return_value.list.return_value.execute.return_value = {
            "items": variants}
        with patch.object(tracker, "youtube", client):
            ready = tracker.reconfirm_older_pending_vods(queue, set(), now=NOW)
        self.assertEqual(ready, set())

    def test_unfinished_premiere_stays_pending_until_actual_end(self):
        queued = video(1)
        client = MagicMock()
        with patch.object(tracker, "youtube", client):
            client.videos.return_value.list.return_value.execute.return_value = {
                "items": [youtube_item(queued, details={"actualStartTime": "2026-10-22T09:00:00Z"})]}
            self.assertEqual(tracker.reconfirm_older_pending_vods([queued], set(), now=NOW), set())
            client.videos.return_value.list.return_value.execute.return_value = {
                "items": [youtube_item(queued, details={"actualStartTime": "2026-10-22T09:00:00Z",
                                                       "actualEndTime": "2026-10-22T09:30:00Z"})]}
            self.assertEqual(tracker.reconfirm_older_pending_vods([queued], set(), now=NOW),
                             {queued["video_id"]})

    def test_short_and_unrelated_renamed_video_are_not_sent(self):
        one, two = video(1), video(2)
        client = MagicMock()
        client.videos.return_value.list.return_value.execute.return_value = {
            "items": [youtube_item(one, duration="PT45S"),
                      youtube_item(two, title="Unrelated clip")]}
        with patch.object(tracker, "youtube", client):
            self.assertEqual(tracker.reconfirm_older_pending_vods([one, two], set(), now=NOW), set())

    def test_missing_api_item_is_not_assumed_published(self):
        client = MagicMock()
        client.videos.return_value.list.return_value.execute.return_value = {"items": []}
        with patch.object(tracker, "youtube", client):
            self.assertEqual(tracker.reconfirm_older_pending_vods([video(1)], set(), now=NOW), set())

    def test_api_failure_preserves_queued_state_and_never_sends(self):
        with tempfile.TemporaryDirectory() as directory:
            bookmark = Path(directory) / "last_run.json"
            initial = blank_state()
            initial["pending"] = [video(1)]
            bookmark.write_text(json.dumps(initial), encoding="utf-8")
            yt = MagicMock()
            yt.videos.return_value.list.return_value.execute.side_effect = RuntimeError("API unavailable")
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), \
                 patch.object(tracker, "LIVE_LAST_RUN_FILE", str(Path(directory) / "no-live.json")), \
                 patch.object(tracker, "youtube", yt), \
                 patch.object(tracker, "send_to_discord") as send:
                with self.assertRaisesRegex(StateError, "Unable to reconfirm"):
                    tracker.run_tracker([], {"UC1"}, run_start=NOW)
                send.assert_not_called()
            self.assertEqual(load_state(bookmark)["pending"], [video(1)])

    def test_recheck_is_one_call_and_rotates_across_large_backlog(self):
        queue = [video(i, published="2026-10-22T01:00:00Z") for i in range(1, 121)]
        client = MagicMock()
        client.videos.return_value.list.return_value.execute.return_value = {"items": []}
        with patch.object(tracker, "youtube", client):
            tracker.reconfirm_older_pending_vods(queue, set(), now=NOW)
            first = set(client.videos.return_value.list.call_args.kwargs["id"].split(","))
            tracker.reconfirm_older_pending_vods(queue, set(), now=NOW + timedelta(minutes=30))
            second = set(client.videos.return_value.list.call_args.kwargs["id"].split(","))
        self.assertEqual(len(first), 50)
        self.assertEqual(len(second), 50)
        self.assertNotEqual(first, second)
        self.assertEqual(client.videos.return_value.list.call_count, 2)

    def test_no_extra_api_calls_when_pending_still_visible_in_scan(self):
        client = MagicMock()
        with patch.object(tracker, "youtube", client):
            self.assertEqual(tracker.reconfirm_older_pending_vods([video(1)],
                                                                {"id-1"}, now=NOW), set())
        client.videos.assert_not_called()

    def test_dry_run_rechecks_without_webhook_or_state_write(self):
        with tempfile.TemporaryDirectory() as directory:
            bookmark = Path(directory) / "last_run.json"
            initial = blank_state()
            initial["pending"] = [video(1)]
            bookmark.write_text(json.dumps(initial), encoding="utf-8")
            original = bookmark.read_bytes()
            yt = MagicMock()
            yt.videos.return_value.list.return_value.execute.return_value = {
                "items": [youtube_item(video(1))]}
            with patch.object(tracker, "LAST_RUN_FILE", str(bookmark)), \
                 patch.object(tracker, "LIVE_LAST_RUN_FILE", str(Path(directory) / "no-live.json")), \
                 patch.object(tracker, "youtube", yt), \
                 patch.object(tracker, "DRY_RUN", True), \
                 patch.object(tracker, "send_to_discord") as send:
                self.assertEqual(tracker.run_tracker([], {"UC1"}, run_start=NOW), [])
                send.assert_not_called()
            self.assertEqual(bookmark.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
