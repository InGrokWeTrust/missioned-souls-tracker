"""Read-only YouTube metadata enrichment for user-approved manual evaluation.

Uses the existing YOUTUBE_API_KEY only in a manually dispatched evaluation run.
Never writes to YouTube, bookmarks, GitHub production or Discord.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

import requests

ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def enrich(samples, *, api_key, getter=None):
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY is required to enrich metadata")
    getter = getter or requests.get
    valid_ids = list(dict.fromkeys(
        s["video_id"] for s in samples if ID_RE.fullmatch(s.get("video_id", ""))))
    details = {}
    for start in range(0, len(valid_ids), 50):
        ids = valid_ids[start:start + 50]
        try:
            response = getter(
                ENDPOINT,
                params={"part": "snippet", "id": ",".join(ids), "key": api_key},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            # Never print the HTTP response URL or exception, which may contain API key.
            raise RuntimeError("YouTube videos.list metadata read failed") from None
        for video in payload.get("items", []):
            if video.get("id") in ids:
                details[video["id"]] = video.get("snippet", {})
    enriched = []
    for sample in samples:
        entry = dict(sample)
        snippet = details.get(sample.get("video_id", ""))
        if snippet:
            entry.update({
                "title": snippet.get("title", entry.get("title", "")),
                "description": snippet.get("description", ""),
                "tags": snippet.get("tags", [])[:30],
                "channel": snippet.get("channelTitle", entry.get("channel", "")),
                "channel_id": snippet.get("channelId", entry.get("channel_id", "")),
                "published_at": snippet.get("publishedAt", entry.get("published_at", "")),
            })
        enriched.append(entry)
    return enriched, len(details)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="vision-unlabelled-candidates.json")
    ap.add_argument("--output", default="vision-enriched-candidates.json")
    args = ap.parse_args()
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise SystemExit("YOUTUBE_API_KEY unavailable: cannot perform metadata enrichment.")
    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows, count = enrich(source["samples"], api_key=key)
    source["samples"] = rows
    source["metadata_enriched"] = count
    Path(args.output).write_text(
        json.dumps(source, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Enriched public snippet metadata for {count} of {len(rows)} candidates.")
    print("Never infer human ground-truth labels from retrieved metadata.")


if __name__ == "__main__":
    main()
