"""Offline tests: Discord only receives the error, never GitHub source links."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from report_failure import classify_error, read_error_log, report_failure

EXAMPLE_RUN_URL="https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39731357929"
QUOTA_LOG="""  ❌ Error fetching HCJB: HttpError 403 when requesting
https://youtube.googleapis.com/youtube/v3/playlistItems?key=SECRET
returned "The request cannot be completed because you have exceeded your quota".
Traceback (most recent call last):
  File "autoreactions.py", line 258, in get_reactions_with_stats
googleapiclient.errors.HttpError: <HttpError 403: quotaExceeded>
"""


def write_log(dir_name, contents):
    path=Path(dir_name)/"tracker-output.log"
    path.write_text(contents,encoding="utf-8")
    return str(path)


class DiscordErrorDiagnosticTests(unittest.TestCase):
    def test_classifies_exact_quota_exceeded_error(self):
        kind=classify_error(QUOTA_LOG,"YouTube Reaction Tracker")
        self.assertIsNotNone(kind)
        self.assertEqual(kind[0],"YouTube API quota exceeded")
        self.assertIn("reaction tracker",kind[1])

    def test_posts_specific_error_not_generic_run_link_or_raw_api_url(self):
        with tempfile.TemporaryDirectory() as directory:
            path=write_log(directory,QUOTA_LOG)
            post=Mock(return_value=Mock(status_code=204))
            result=report_failure(
                webhook_url="https://discord.invalid/test",
                workflow="YouTube Reaction Tracker",
                run_url=EXAMPLE_RUN_URL,
                log_path=path,post=post)
        self.assertTrue(result)
        payload=post.call_args.kwargs["json"]
        body=json.dumps(payload)
        self.assertIn("YouTube API quota exceeded",body)
        self.assertNotIn("Tracker workflow failed",body)
        self.assertNotIn("Check the job logs",body)
        self.assertNotIn("github.com",body)
        self.assertNotIn("youtube.googleapis.com",body)
        self.assertNotIn("SECRET",body)
        self.assertNotIn("39731357929",body)
        self.assertNotIn("traceback",body.lower())

    def test_missing_log_suppresses_vague_discord_notification(self):
        post=Mock()
        ok=report_failure(webhook_url="https://discord.invalid",
                          log_path="/path/that/does/not/exist",post=post)
        self.assertFalse(ok)
        post.assert_not_called()

    def test_unknown_exception_suppresses_generic_discord_message(self):
        with tempfile.TemporaryDirectory() as d:
            path=write_log(d,"Traceback (most recent call last):\n"
                             "ValueError: some new bug; https://github.com/run/123\n")
            post=Mock()
            ok=report_failure(webhook_url="https://discord.invalid",log_path=path,
                              post=post)
        self.assertFalse(ok)
        post.assert_not_called()

    def test_an_earlier_quota_warning_doesnt_hide_a_different_fatal_error(self):
        source=("Warning: quotaExceeded fetching old video\n"
                "Traceback (most recent call last):\n"
                "  File 'script.py'\nTypeError: unexpected data shape\n")
        self.assertIsNone(classify_error(source))

    def test_classifies_youtube_api_key_error(self):
        result=classify_error("googleapiclient.errors.HttpError: API_KEY_INVALID")
        self.assertEqual(result[0],"YouTube API key configuration error")

    def test_classifies_corrupt_bookmark_without_leaking_details(self):
        result=classify_error("ValueError: invalid last_run.json bookmark")
        self.assertEqual(result[0],"Tracker bookmark cannot be restored")

    def test_classifies_discord_delivery_problem(self):
        result=classify_error("ERROR: Discord request failed: ConnectionError")
        self.assertEqual(result[0],"Discord notification delivery failed")

    def test_classifies_youtube_temporary_unavailability(self):
        result=classify_error("googleapiclient.errors.HttpError 503",workflow="Check Live Streams")
        self.assertEqual(result[0],"YouTube API temporarily unavailable")
        self.assertIn("live-stream checker",result[1])

    def test_reads_only_bounded_log_tail(self):
        with tempfile.TemporaryDirectory() as d:
            path=write_log(d,"x"*600_000+QUOTA_LOG)
            fragment=read_error_log(path)
        self.assertLessEqual(len(fragment.encode("utf-8")),512_000)
        self.assertIn("quotaExceeded",fragment)

    def test_webhook_failure_returns_false_without_an_unhandled_error(self):
        with tempfile.TemporaryDirectory() as d:
            path=write_log(d,QUOTA_LOG)
            post=Mock(return_value=Mock(status_code=500))
            ok=report_failure(webhook_url="https://discord.invalid",
                              log_path=path,post=post)
        self.assertFalse(ok)
        post.assert_called_once()

    def test_no_webhook_does_not_attempt_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            path=write_log(d,QUOTA_LOG)
            with patch.dict(os.environ,{"ALERT_WEBHOOK_URL":"",
                                        "DISCORD_WEBHOOK_URL":""}):
                self.assertFalse(report_failure(log_path=path))

    def test_legacy_run_url_environment_is_never_posted(self):
        with tempfile.TemporaryDirectory() as d:
            path=write_log(d,QUOTA_LOG)
            post=Mock(return_value=Mock(status_code=204))
            with patch.dict(os.environ,{"RUN_URL":EXAMPLE_RUN_URL}):
                report_failure(webhook_url="https://discord.invalid",
                               workflow="YouTube Reaction Tracker",
                               log_path=path,post=post)
        self.assertNotIn("github",json.dumps(post.call_args.kwargs["json"]))

    def test_health_alert_requires_separate_webhook_not_reaction_webhook(self):
        with tempfile.TemporaryDirectory() as d:
            path=write_log(d,QUOTA_LOG)
            post=Mock()
            with patch.dict(os.environ, {
                "ALERT_WEBHOOK_URL": "",
                "DISCORD_WEBHOOK_URL": "https://discord.invalid/reaction-feed",
            }):
                self.assertFalse(report_failure(log_path=path,post=post))
        post.assert_not_called()

    def test_current_discord_http_429_detected(self):
        result=classify_error("RuntimeError: Discord HTTP 429 for video", 
                              "YouTube Reaction Tracker")
        self.assertEqual(result[0],"Discord notification delivery failed")

    def test_current_live_http_500_detected(self):
        result=classify_error("RuntimeError: Discord live HTTP 500 for video",
                              "Check Live Streams")
        self.assertEqual(result[0],"Discord notification delivery failed")

    def test_invalid_pending_queue_diagnostic(self):
        result=classify_error("ValueError: Invalid pending-video bookmark")
        self.assertEqual(result[0],"Reaction notification queue requires attention")

    def test_durable_history_error_diagnostic(self):
        result=classify_error("RuntimeError: Durable notification history unavailable")
        self.assertEqual(result[0],"Confirmed-delivery history unavailable")

    def test_unrelated_error_remains_silent(self):
        with tempfile.TemporaryDirectory() as d:
            path=write_log(d,"Traceback (most recent call last):\\n"
                             "TypeError: unexpected data format")
            post=Mock()
            self.assertFalse(report_failure(webhook_url="https://discord.invalid/health",
                                            log_path=path,post=post))
        post.assert_not_called()

    def test_live_checker_quota_error_uses_correct_workflow_name(self):
        with tempfile.TemporaryDirectory() as d:
            path=write_log(d,QUOTA_LOG)
            post=Mock(return_value=Mock(status_code=204))
            report_failure(webhook_url="https://discord.invalid",
                           workflow="Check Live Streams",log_path=path,post=post)
        payload=post.call_args.kwargs["json"]
        self.assertIn("live-stream checker",payload["embeds"][0]["description"])


if __name__=="__main__":
    unittest.main()
