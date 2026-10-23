"""Verify safe production workflow wiring without dispatching workflows."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / ".github/workflows/youtube-tracker.yml"
LIVE = ROOT / ".github/workflows/check_live_streams.yml"


class WorkflowSafetyTests(unittest.TestCase):
    def test_dry_run_setting_is_at_job_scope_for_all_steps(self):
        for path in (TRACKER, LIVE):
            with self.subTest(workflow=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("    runs-on: ubuntu-latest\n    env:\n      DRY_RUN:", source)
                self.assertIn("inputs.dry_run && 'true'", source)
                self.assertIn("vars.DRY_RUN || 'false'", source)
                self.assertIn("      dry_run:", source)
                self.assertIn("type: boolean", source)
                self.assertIn("default: false", source)

    def test_dry_run_cannot_upload_bookmark_artifact(self):
        for path in (TRACKER, LIVE):
            with self.subTest(workflow=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("if: \${{ always() && env.DRY_RUN != 'true' }}", source)
                self.assertIn("uses: actions/upload-artifact@v4", source)

    def test_dry_run_cannot_trigger_error_health_message(self):
        for path in (TRACKER, LIVE):
            with self.subTest(workflow=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("failure() && github.ref == 'refs/heads/main' "
                              "&& env.DRY_RUN != 'true'", source)

    def test_uses_pipefail_and_logs_to_preserve_exit_status(self):
        for path, filename in ((TRACKER, "reaction-tracker-output.log"),
                               (LIVE, "live-checker-output.log")):
            with self.subTest(workflow=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("set -o pipefail", source)
                self.assertIn(f"2>&1 | tee {filename}", source)
                self.assertIn(f"ALERT_LOG_FILE: {filename}", source)

    def test_dedicated_health_webhook_never_reaction_feed_fallback(self):
        for path in (TRACKER, LIVE):
            with self.subTest(workflow=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("ALERT_WEBHOOK_URL: \${{ secrets.ALERT_WEBHOOK_URL }}", source)
                self.assertIn("run: python report_failure.py", source)


if __name__ == "__main__":
    unittest.main()
