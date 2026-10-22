"""Collect a diverse, UNLABELLED sample from public RSS feeds of tracked reactors.

No YouTube Data API key, image model, Discord credentials or production writes.
A candidate must be human-reviewed before it can be included in accuracy metrics.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import csv
from pathlib import Path
import random
import re
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tracked_channels.json"
OUT = ROOT / "vision-unlabelled-candidates.json"
REVIEW_CSV = ROOT / "vision-review-sheet.csv"
STATE = ROOT / "last_run.json"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MAX_CHANNELS = 65
MAX_PER_CHANNEL = 4
TARGET = 150

SEED_EXAMPLES = [
    ("hf03Voz2QBM", "Taylor John Hardin Reactions",
     "Nitro Neisha TEARS It Up On This Classic!"),
    ("kH6vzMgvfS0", "WILBURN MUSIC REACTIONS",
     "THIS 11 YEAR OLD DRUMMER IS UNREAL - MISSIONED SOULS"),
    ("Lr4gUjZVjT0", "Taylor John Hardin Reactions",
     "Nene Royal music reaction"),
    ("n9ZfJX8mDJc", "Taylor John Hardin Reactions",
     "Guitar tunings discussion"),
    ("tHS3hkb7uV0", "Taylor John Hardin Reactions",
     "One Direction reaction"),
]


def parse_feed(xml_text, channel_id, channel_title, limit=MAX_PER_CHANNEL):
    root = ET.fromstring(xml_text)
    results = []
    for entry in root.findall(ATOM + "entry"):
        video_id = entry.findtext(YT + "videoId")
        if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            continue
        title = entry.findtext(ATOM + "title") or ""
        published = entry.findtext(ATOM + "published") or ""
        results.append({
            "video_id": video_id, "channel_id": channel_id, "channel": channel_title,
            "title": title, "published_at": published,
            "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "description": "", "tags": [],
            "ground_truth": None, "reviewed_by": None,
            "source": "public YouTube RSS",
        })
        if len(results) >= limit:
            break
    return results


def fetch_feed(entry):
    channel_id, title = entry
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id
    try:
        response = requests.get(url, timeout=10, headers={
            "User-Agent": "Missioned-Souls-Evaluation/1.0"
        })
        response.raise_for_status()
        return parse_feed(response.text, channel_id, title), None
    except (requests.RequestException, ET.ParseError) as exc:
        return [], f"{title}: {type(exc).__name__}"


def collect(channels: dict, *, getter=fetch_feed, target=TARGET, max_channels=MAX_CHANNELS):
    valid = [(channel_id, info.get("title", channel_id))
             for channel_id, info in channels.items()
             if isinstance(info, dict)
             and "missioned souls" not in info.get("title", "").lower()]
    random.Random(20261022).shuffle(valid)
    selected = valid[:max_channels]
    by_channel, failures = {}, []
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(getter, entry): entry for entry in selected}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                items, error = future.result()
                if error:
                    failures.append(error)
                by_channel[entry[0]] = items
            except Exception as exc:
                failures.append(f"{entry[1]}: {type(exc).__name__}")
                by_channel[entry[0]] = []

    # Round-robin limits dominance by channels uploading frequently.
    all_rows, used = [], set()
    for i in range(MAX_PER_CHANNEL):
        for cid, title in selected:
            entries = by_channel.get(cid, [])
            if i < len(entries) and entries[i]["video_id"] not in used:
                all_rows.append(entries[i])
                used.add(entries[i]["video_id"])
                if len(all_rows) >= target:
                    return all_rows, len(selected), failures
    return all_rows, len(selected), failures


def saved_state_candidates(path=STATE, *, limit=200):
    """Harvest *unlabelled* IDs from the latest production bookmark artifact.

    This is a read-only fallback if YouTube blocks public RSS feeds on CI.
    An ID recorded in seen_video_ids is NOT ground truth and may include
    videos that are not reactions; every row still needs independent review.
    """
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = payload.get("seen_video_ids", [])
    if not isinstance(ids, list):
        return []
    results = []
    for vid in reversed(ids):
        if isinstance(vid, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            results.append({
                "video_id": vid, "channel": "", "channel_id": "",
                "title": "", "published_at": "",
                "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "description": "", "tags": [], "ground_truth": None,
                "reviewed_by": None, "source": "read-only production video-ID history",
            })
        if len(results) >= limit:
            break
    return results


def create_review_csv(rows, path=REVIEW_CSV):
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["video_id", "YouTube video URL", "thumbnail URL", "channel",
                         "title", "source", "ground_truth", "reviewed_by", "review_notes"])
        for row in rows:
            vid = row["video_id"]
            writer.writerow([vid, f"https://www.youtube.com/watch?v={vid}",
                             row["thumbnail_url"], row.get("channel", ""),
                             row.get("title", ""), row.get("source", ""),
                             "", "", ""])


def main():
    channels = json.loads(TRACKED.read_text(encoding="utf-8"))["channels"]
    rows, attempted, failures = collect(channels)
    known = {item["video_id"] for item in rows}
    fallback = saved_state_candidates()
    for video in fallback:
        if len(rows) >= TARGET:
            break
        if video["video_id"] not in known:
            rows.append(video)
            known.add(video["video_id"])
    for video_id, channel, title in SEED_EXAMPLES:
        if video_id not in known:
            rows.insert(0, {
                "video_id": video_id, "channel": channel, "channel_id": "",
                "title": title, "published_at": "",
                "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "description": "", "tags": [], "ground_truth": None,
                "reviewed_by": None, "source": "manually selected example",
            })
            known.add(video_id)
    result = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "label_status": "UNLABELLED - NOT GROUND TRUTH",
        "feed_channels_attempted": attempted,
        "feed_failures": len(failures),
        "sample_count": len(rows),
        "state_history_candidates": len(fallback),
        "samples": rows,
        "failures_sample": failures[:15],
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    create_review_csv(rows)
    print(f"Collected {len(rows)} UNLABELLED candidates from "
          f"{attempted} attempted feeds; {len(failures)} failed.")
    print("IMPORTANT: No recall/precision can be calculated before human review.")
    print(f"Report: {OUT.name}; review CSV: {REVIEW_CSV.name}")
    print(f"Fallback state IDs available: {len(fallback)} (not labelled)")


if __name__ == "__main__":
    main()
