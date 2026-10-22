"""Shared Discord webhook sender with bounded retries for transient failures."""

import time
import requests


def post_webhook(url, payload, *, attempts=3, timeout=10, request_post=None, sleeper=None):
    """Return True only on Discord success; never acknowledge failed delivery."""
    post = request_post if request_post is not None else requests.post
    sleep = sleeper if sleeper is not None else time.sleep

    for attempt in range(attempts):
        try:
            response = post(url, json=payload, timeout=timeout)
        except requests.RequestException as error:
            print(f"❌ Discord request failed: {error}")
            if attempt == attempts - 1:
                return False
            sleep(min(2 ** attempt, 10))
            continue

        if response.status_code in (200, 204):
            return True

        print(f"❌ Discord returned HTTP {response.status_code}")
        if response.status_code != 429 and response.status_code < 500:
            return False
        if attempt == attempts - 1:
            return False

        try:
            delay = float(response.headers.get("Retry-After", 2 ** attempt))
        except (ValueError, TypeError, AttributeError):
            delay = 2 ** attempt
        sleep(max(0, min(delay, 30)))

    return False
