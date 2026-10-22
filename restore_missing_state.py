"""Fail-closed fallback when the Actions artifact-download action finds no state.

Reads the newest branch-specific bookmark artifact directly through GitHub's
API, preserving the existing state if a file is already present. Never seeds
empty bookmarks and never invokes YouTube or Discord.
"""
import io
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import requests

ARTIFACTS = {
    "reactions": ("last-run-state", "last_run.json", ".github/workflows/youtube-tracker.yml"),
    "live": ("live-last-run-state", "live_last_run.json", ".github/workflows/check_live_streams.yml"),
}


def _validate(kind, raw):
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("Saved bookmark exceeds safe size limit")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Bookmark must contain a JSON object")
    if kind == "reactions":
        if (data.get("version") != 2 or
                any(not isinstance(data.get(field), list)
                    for field in ("seen_video_ids", "known_channel_ids", "pending"))):
            raise ValueError("Invalid reaction bookmark schema")
    else:
        if not isinstance(data.get("sent_video_ids"), list):
            raise ValueError("Invalid live bookmark schema")
    return data


def _read_json(session, url, headers):
    response = session.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def recover(kind, *, directory=".", session=None, branch=None, retries=3):
    """Restore a missing artifact, or leave an existing bookmark untouched.

    A missing/corrupt GitHub source always raises; no empty state is generated.
    """
    if kind not in ARTIFACTS:
        raise ValueError("Unknown bookmark kind")
    artifact_name, filename, workflow_path = ARTIFACTS[kind]
    destination = Path(directory) / filename
    if destination.exists():
        print(f"✅ Existing {kind} bookmark is present; fallback not needed.")
        return False

    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    if not repo or not token or "/" not in repo:
        raise RuntimeError("Missing GitHub repository or token for artifact recovery")
    branch = branch or os.getenv("GITHUB_REF_NAME") or "main"
    base = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    root = f"{base}/repos/{repo}"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    session = session or requests.Session()
    failure = None

    for attempt in range(retries):
        try:
            candidates = []
            for page in range(1, 4):
                endpoint = (f"{root}/actions/artifacts?name={quote(artifact_name)}"
                            f"&per_page=100&page={page}")
                results = _read_json(session, endpoint, headers).get("artifacts", [])
                candidates += [
                    item for item in results
                    if item.get("name") == artifact_name
                    and not item.get("expired")
                    and (item.get("workflow_run") or {}).get("head_branch") == branch
                ]
                if len(results) < 100 or candidates:
                    break
            if not candidates:
                raise RuntimeError("No unexpired matching production artifact found")
            chosen = max(candidates, key=lambda x: x.get("created_at", ""))
            run_id = (chosen.get("workflow_run") or {}).get("id")
            if not run_id:
                raise ValueError("Artifact lacks its workflow-run identifier")
            run = _read_json(session, f"{root}/actions/runs/{run_id}", headers)
            if run.get("head_branch") != branch or run.get("path") != workflow_path:
                raise ValueError("Artifact belongs to an unexpected branch or workflow")

            # requests strips GitHub bearer credentials from cross-host redirects.
            response = session.get(
                f"{root}/actions/artifacts/{chosen['id']}/zip",
                headers=headers, timeout=60)
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                if archive.namelist() != [filename]:
                    raise ValueError("Unexpected bookmark ZIP contents")
                raw = archive.read(filename)
            _validate(kind, raw)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    "wb", dir=destination.parent,
                    prefix=".bookmark-recovery-", delete=False
                ) as handle:
                    temp_path = handle.name
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, destination)
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
            print(f"✅ Recovered {kind} bookmark from GitHub artifact "
                  f"{chosen['id']} (run {run_id}), no notification sent.")
            return True
        except (requests.RequestException, ValueError, RuntimeError,
                json.JSONDecodeError, zipfile.BadZipFile) as exc:
            failure = exc
            if attempt + 1 < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(
        f"Could not recover {kind} bookmark after {retries} attempts; "
        "refusing to run notification tracker."
    ) from failure


if __name__ == "__main__":
    if len(sys.argv) < 2 or any(k not in ARTIFACTS for k in sys.argv[1:]):
        raise SystemExit("Usage: python restore_missing_state.py reactions [live]")
    for kind in sys.argv[1:]:
        recover(kind)
