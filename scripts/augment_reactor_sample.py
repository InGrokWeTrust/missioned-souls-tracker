"""Optionally expand saved-ID candidates with title-ambiguous reactor uploads.

Run only in an explicitly user-dispatched, read-only YouTube API evaluation.
Max 65 playlist.list calls (~65 standard units), 5 recent uploads per channel.
NO Discord, production bookmark writes, or automatic labels.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import re

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "https://www.googleapis.com/youtube/v3/playlistItems"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def augment(dataset, channels, *, api_key, getter=None,
            max_channels=65, max_new=85, max_total=200):
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY is required for additional read-only sampling")
    if not 1 <= max_channels <= 65 or not 1 <= max_new <= 100:
        raise ValueError("Sampling call limits exceeded")
    requester = getter or requests.get
    rows = [dict(v) for v in dataset["samples"]]
    seen = {v["video_id"] for v in rows}
    available = [(cid, obj.get("title", cid)) for cid, obj in channels.items()
                 if isinstance(obj, dict) and "missioned souls" not in obj.get("title", "").lower()]
    random.Random(20261022).shuffle(available)
    attempts = 0
    failures = 0
    added = 0
    for cid, channel_title in available[:max_channels]:
        if added >= max_new or len(rows) >= max_total:
            break
        attempts += 1
        playlist_id = "UU" + cid[2:]
        try:
            response = requester(
                API, timeout=20,
                params={"key": api_key, "part": "snippet,contentDetails",
                        "playlistId": playlist_id, "maxResults": 5}
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            failures += 1
            # Never print exception URLs which may contain the API key.
            continue
        for item in payload.get("items", []):
            snippet = item.get("snippet", {})
            details = item.get("contentDetails", {})
            vid = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
            if not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid) or vid in seen:
                continue
            title = snippet.get("title", "") or ""
            if "missioned souls" in title.lower():
                # This is the *ambiguous* negative-candidate stratum; retain
                # visible positive titles in the existing saved-ID set.
                continue
            rows.append({
                "video_id": vid, "channel": channel_title, "channel_id": cid,
                "title": title, "published_at": details.get("videoPublishedAt")
                or snippet.get("publishedAt", ""),
                "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "description": "", "tags": [], "ground_truth": None,
                "reviewed_by": None,
                "source": "manual read-only YouTube uploads sample (no band in title)",
            })
            seen.add(vid)
            added += 1
            if added >= max_new or len(rows) >= max_total:
                break
    result = dict(dataset)
    result["samples"] = rows
    result["sample_count"] = len(rows)
    result["ambiguous_playlist_candidates_added"] = added
    result["extra_playlist_calls"] = attempts
    result["extra_playlist_failures"] = failures
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="vision-unlabelled-candidates.json")
    ap.add_argument("--output", default="vision-expanded-candidates.json")
    args = ap.parse_args()
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise SystemExit("YOUTUBE_API_KEY unavailable: no extra sample was collected.")
    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    channels = json.loads((ROOT / "tracked_channels.json").read_text(encoding="utf-8"))["channels"]
    result = augment(source, channels, api_key=key)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    print(f"Sample now has {result['sample_count']} unlabelled candidates; "
          f"{result['ambiguous_playlist_candidates_added']} new ambiguous uploads "
          f"from {result['extra_playlist_calls']} playlist calls "
          f"({result['extra_playlist_failures']} failed).")
    print("These ambiguous uploads are NOT confirmed negatives; human review required.")


if __name__ == "__main__":
    main()
