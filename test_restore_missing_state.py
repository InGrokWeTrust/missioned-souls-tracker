"""Offline tests: no GitHub, YouTube, Discord, or production state writes."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import restore_missing_state as recovery


def zip_bookmark(filename, contents, extra=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as f:
        f.writestr(filename, contents)
        if extra:
            f.writestr("../unexpected", "unsafe")
    return buf.getvalue()


def response(*, data=None, content=None):
    value = Mock()
    value.raise_for_status.return_value = None
    value.json.return_value = data
    value.content = content
    return value


def fake_api(kind="reactions", *, branch="main", archive=None,
             missing=False, run_path=None, older=False):
    name, filename, expected_path = recovery.ARTIFACTS[kind]
    payload = ({
        "version": 2, "last_published_at": "2026-10-22T00:00:00Z",
        "seen_video_ids": ["Etztcp21KUw"], "known_channel_ids": ["channel"], "pending": []
    } if kind == "reactions" else {
        "sent_video_ids": ["live-1"], "last_check": "2026-10-22T00:00:00Z"
    })
    archive = archive if archive is not None else zip_bookmark(
        filename, json.dumps(payload))
    candidates = [] if missing else [
        {"id": 101, "name": name, "expired": False,
         "created_at": "2026-10-22T18:00:00Z",
         "workflow_run": {"id": 111, "head_branch": branch}},
    ]
    if older:
        candidates.append(
            {"id": 102, "name": name, "expired": False,
             "created_at": "2026-10-22T14:00:00Z",
             "workflow_run": {"id": 112, "head_branch": branch}})
    api = Mock()
    def get(url, **kwargs):
        if "/actions/artifacts?" in url:
            return response(data={"artifacts": candidates})
        if "/actions/runs/111" in url:
            return response(data={"head_branch": branch,
                                  "path": run_path or expected_path})
        if "/actions/artifacts/101/zip" in url:
            return response(content=archive)
        raise AssertionError("Unexpected API request: " + url)
    api.get.side_effect = get
    return api


class MissingArtifactFallbackTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict("os.environ", {
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_TOKEN": "mock-token",
            "GITHUB_REF_NAME": "main",
        })
        env.start()
        self.addCleanup(env.stop)

    def test_restores_newest_reaction_bookmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = fake_api(older=True)
            self.assertTrue(recovery.recover("reactions", directory=tmp,
                                            session=api, retries=1))
            restored = json.loads((Path(tmp) / "last_run.json").read_text())
            self.assertEqual(restored["seen_video_ids"], ["Etztcp21KUw"])
            self.assertEqual(restored["pending"], [])
            self.assertIn("/actions/artifacts/101/zip", 
                          [c.args[0] for c in api.get.call_args_list][-1])

    def test_restores_live_bookmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(recovery.recover("live", directory=tmp,
                                            session=fake_api("live"), retries=1))
            restored = json.loads((Path(tmp) / "live_last_run.json").read_text())
            self.assertEqual(restored["sent_video_ids"], ["live-1"])

    def test_existing_state_untouched_and_no_api_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last_run.json"
            path.write_text("already here")
            session = Mock()
            self.assertFalse(recovery.recover("reactions", directory=tmp, session=session))
            self.assertEqual(path.read_text(), "already here")
            session.get.assert_not_called()

    def test_no_artifact_fails_closed_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(recovery.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "refusing to run"):
                recovery.recover("reactions", directory=tmp,
                                 session=fake_api(missing=True), retries=3)
            self.assertFalse((Path(tmp) / "last_run.json").exists())
            self.assertEqual(sleep.call_count, 2)

    def test_wrong_workflow_and_branch_fail_closed(self):
        for api in (fake_api(run_path=".github/workflows/other.yml"),
                    fake_api(branch="different")):
            with self.subTest(api=api), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(RuntimeError, "refusing to run"):
                    recovery.recover("reactions", directory=tmp,
                                     session=api, retries=1)
                self.assertFalse((Path(tmp) / "last_run.json").exists())

    def test_corrupt_or_extra_zip_entries_fail_closed(self):
        for archive in (b"not a ZIP", zip_bookmark("last_run.json", "{}", extra=True),
                        zip_bookmark("last_run.json", '{"version":0}')):
            with self.subTest(archive_size=len(archive)), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(RuntimeError, "refusing to run"):
                    recovery.recover("reactions", directory=tmp, retries=1,
                                     session=fake_api(archive=archive))
                self.assertFalse((Path(tmp) / "last_run.json").exists())


if __name__ == "__main__":
    unittest.main()
