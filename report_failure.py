"""Best-effort failure alert for production GitHub Actions workflows."""
import os
import requests


def report_failure(webhook_url=None, workflow=None, run_url=None, post=None):
    url = webhook_url if webhook_url is not None else (
        os.environ.get("ALERT_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL")
    )
    if not url:
        print("⚠️ No failure alert webhook configured.")
        return False
    name = workflow or os.environ.get("GITHUB_WORKFLOW", "Missioned Souls tracker")
    link = run_url or os.environ.get("RUN_URL", "")
    payload = {
        "username": "Missioned Souls Tracker Health",
        "embeds": [{
            "title": "⚠️ Tracker workflow failed",
            "description": f"**{name}** failed. Check the job logs before retrying.\n{link}",
            "color": 0xf39c12
        }]
    }
    try:
        request = post if post is not None else requests.post
        response = request(url, json=payload, timeout=10)
        if response.status_code in (200, 204):
            print("✅ Failure alert delivered.")
            return True
        print(f"⚠️ Failure alert failed: HTTP {response.status_code}")
    except requests.RequestException as error:
        print(f"⚠️ Could not send failure alert: {error}")
    return False


if __name__ == "__main__":
    report_failure()
