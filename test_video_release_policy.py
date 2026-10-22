import unittest

from video_release_policy import is_published_vod


class VideoReleasePolicyTests(unittest.TestCase):
    def video(self, status="none", details=None, confirmed=True):
        return {
            "live_broadcast_content": status,
            "live_streaming_details": details,
            "metadata_confirmed": confirmed,
        }

    def test_regular_published_vod(self):
        self.assertTrue(is_published_vod(self.video()))

    def test_upcoming_notify_me_never_posts(self):
        self.assertFalse(is_published_vod(self.video(status="upcoming")))
        self.assertFalse(is_published_vod(self.video(status="upcoming", details={})))

    def test_live_premiere_never_posts(self):
        self.assertFalse(is_published_vod(self.video(status="live")))

    def test_not_finished_even_if_status_is_none(self):
        self.assertFalse(is_published_vod(self.video(details={"scheduledStartTime": "2026-10-22T10:00:00Z"})))
        self.assertFalse(is_published_vod(self.video(details={"actualStartTime": "2026-10-22T10:00:00Z"})))

    def test_finished_broadcast_is_eligible(self):
        self.assertTrue(is_published_vod(self.video(details={"actualEndTime": "2026-10-22T10:30:00Z"})))

    def test_completed_premiere_needs_non_live_status(self):
        ended = {"actualEndTime": "2026-10-22T10:30:00Z"}
        self.assertFalse(is_published_vod(self.video(status="live", details=ended)))
        self.assertFalse(is_published_vod(self.video(status="upcoming", details=ended)))
        self.assertTrue(is_published_vod(self.video(status="none", details=ended)))

    def test_unconfirmed_metadata_fails_closed(self):
        self.assertFalse(is_published_vod(self.video(confirmed=False)))
        self.assertFalse(is_published_vod(self.video(status="unknown")))
        self.assertFalse(is_published_vod(self.video(details={})))

if __name__ == "__main__":
    unittest.main()
