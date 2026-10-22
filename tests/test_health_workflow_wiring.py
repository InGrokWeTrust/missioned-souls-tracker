"""Keep both production workflow diagnostic capture paths wired correctly."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


class WorkflowDiagnosticWiringTests(unittest.TestCase):
    def test_reaction_run_preserves_failure_status_and_captures_log(self):
        yml=(ROOT/".github/workflows/youtube-tracker.yml").read_text()
        self.assertIn("set -o pipefail",yml)
        self.assertIn("python autoreactions.py 2>&1 | tee reaction-tracker-output.log",yml)
        self.assertIn("ALERT_LOG_FILE: reaction-tracker-output.log",yml)
        self.assertIn("failure() && github.ref == 'refs/heads/main'",yml)
        self.assertNotIn("RUN_URL:",yml)

    def test_live_run_preserves_failure_status_and_captures_log(self):
        yml=(ROOT/".github/workflows/check_live_streams.yml").read_text()
        self.assertIn("set -o pipefail",yml)
        self.assertIn("python check_live_streams.py 2>&1 | tee live-checker-output.log",yml)
        self.assertIn("ALERT_LOG_FILE: live-checker-output.log",yml)
        self.assertIn("failure() && github.ref == 'refs/heads/main'",yml)
        self.assertNotIn("RUN_URL:",yml)


if __name__=="__main__":
    unittest.main()
