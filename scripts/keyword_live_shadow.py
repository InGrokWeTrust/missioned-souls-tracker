"""One-off, read-only live YouTube keyword reconnaissance.

This script NEVER imports webhook_delivery or the production tracker.
No Discord calls, bookmark writes, paid AI, or GitHub mutations.
A GitHub PR job may run it with only YOUTUBE_API_KEY (read-only).
Results are *unreviewed candidates* until an independent person verifies videos.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import sys

import requests

from keyword_detector import classify

ROOT = Path(__file__).resolve().parents[1]
CHANNELS_PATH = ROOT / "tracked_channels.json"
OUTPUT = ROOT / "keyword-live-shadow-report.json"
API_ROOT = "https://www.googleapis.com/youtube/v3"
MAX_UPLOADS = 5
MAX_PARALLEL = 5
MAX_ROWS = 700
CONTROL_IDS = ["hf03Voz2QBM", "kH6vzMgvfS0", "Lr4gUjZVjT0",
               "n9ZfJX8mDJc", "tHS3hkb7uV0"]
ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
OFFICIAL_CHANNEL = "UCeTfwcnUVurw7Qrk_KF5Idw"


def api_get(endpoint, params, key, *, getter=requests.get):
    if not key:
        raise ValueError("Missing YouTube API key")
    try:
        response = getter(
            API_ROOT + "/" + endpoint,
            params={**params, "key": key},
            timeout=22,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Malformed YouTube API response")
        return result
    except (requests.RequestException, ValueError, TypeError):
        # Never include request URL, HTTP response, body, or exception string:
        # the URL may contain the YOUTUBE_API_KEY parameter.
        raise RuntimeError("Read-only YouTube API request failed") from None


def fetch_uploads(cid, channel, *, key, getter=requests.get):
    if not cid.startswith("UC") or len(cid) != 24:
        return None
    obj = api_get("playlistItems", {
        "part":"snippet,contentDetails",
        "playlistId":"UU" + cid[2:],
        "maxResults": MAX_UPLOADS,
    }, key, getter=getter)
    rows = []
    for entry in obj.get("items", []):
        snippet=entry.get("snippet") or {}
        details=entry.get("contentDetails") or {}
        vid=details.get("videoId") or (snippet.get("resourceId") or {}).get("videoId")
        if not isinstance(vid,str) or not ID.fullmatch(vid):
            continue
        rows.append({
            "video_id":vid, "title":snippet.get("title") or "",
            "channel":channel, "channel_id":cid,
            "published_at":details.get("videoPublishedAt") or snippet.get("publishedAt") or "",
            "url":"https://www.youtube.com/watch?v=" + vid,
            "source":"tracked uploads playlist",
        })
    return rows


def seconds_iso(duration):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",duration or "")
    return ((int(m.group(1) or 0) * 3600) + (int(m.group(2) or 0) * 60) +
            int(m.group(3) or 0)) if m else None


def classify_actual(row, item):
    """Separate exclusions from evidence; keep original title and video ID."""
    details = item.get("contentDetails") or {}
    snippet = item.get("snippet") or {}
    duration = seconds_iso(details.get("duration"))
    title = snippet.get("title") or row["title"]
    has_live = bool(item.get("liveStreamingDetails"))
    broadcast = snippet.get("liveBroadcastContent", "none")
    excluded_reason = None
    if row.get("channel_id") == OFFICIAL_CHANNEL:
        excluded_reason = "official band channel (not a reactor)"
    elif broadcast in ("live", "upcoming") or has_live:
        excluded_reason = "live/upcoming/archived stream"
    elif re.search(r"(?:#shorts?\b|\bshorts\b)",title,re.I):
        excluded_reason = "labelled Shorts"
    elif duration is not None and 0 < duration < 120:
        excluded_reason = "under two minutes"
    description = snippet.get("description") or ""
    tags = snippet.get("tags") or []
    result = classify(title, description, tags)
    return {
        **row,
        "title": title,
        "duration_sec":duration,
        "tier": "excluded" if excluded_reason else result["tier"],
        "would_auto_post": False,
        "excluded_reason": excluded_reason,
        "evidence":result["evidence"],
        "reasons":result["reasons"],
        "description_excerpt":description[:2200],
        "description_length":len(description),
        "tags":tags[:30] if isinstance(tags,list) else [],
        "title_has_band": bool(re.search(r"\bmissioned[\s_-]*souls\b",title,re.I)),
        "matched_without_band_title": not bool(re.search(
            r"\bmissioned[\s_-]*souls\b",title,re.I)) and result["tier"] in ("strong","review"),
        "source": row.get("source",""),
    }


def run(channels, *, key, fetcher=fetch_uploads, api=api_get, controls=CONTROL_IDS):
    if not isinstance(channels, dict) or not channels:
        raise ValueError("No tracked channels")
    channel_results = {}
    failed_channels = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        jobs={pool.submit(fetcher,cid,
                          obj.get("title",cid) if isinstance(obj,dict) else str(obj),
                          key=key):cid for cid,obj in channels.items()}
        for future in as_completed(jobs):
            cid=jobs[future]
            try:
                videos=future.result()
                if videos is None:
                    failed_channels.append(cid)
                else:
                    channel_results[cid]=videos
            except Exception:
                failed_channels.append(cid)

    all_rows=[]
    used=set()
    for cid in channels:
        for row in channel_results.get(cid,[]):
            vid=row["video_id"]
            if vid not in used and len(all_rows)<MAX_ROWS:
                used.add(vid)
                all_rows.append(row)

    details={}
    metadata_calls=0
    targets=list(dict.fromkeys([x["video_id"] for x in all_rows]+list(controls)))
    for i in range(0,len(targets),50):
        metadata_calls+=1
        data=api("videos",{"part":"snippet,contentDetails,liveStreamingDetails",
                           "id":",".join(targets[i:i+50])},key)
        for v in data.get("items",[]):
            if v.get("id") in targets:
                details[v["id"]]=v

    rows=[]
    for v in all_rows:
        d=details.get(v["video_id"])
        if d is None:
            rows.append({**v,"tier":"metadata_unavailable", "would_auto_post":False,
                         "excluded_reason":None,"evidence":[],"reasons":["metadata missing"]})
        else:
            rows.append(classify_actual(v,d))

    control_rows=[]
    for vid in controls:
        item=details.get(vid)
        if item is None:
            control_rows.append({"video_id":vid,"found":False,
                                 "note":"Not returned by public videos.list"})
            continue
        sn=item.get("snippet") or {}
        row={
            "video_id":vid,"url":"https://www.youtube.com/watch?v="+vid,
            "title":sn.get("title",""), "channel":sn.get("channelTitle",""),
            "channel_id":sn.get("channelId",""), "published_at":sn.get("publishedAt",""),
            "source":"explicit historical control (outside latest 5 possible)",
        }
        control_rows.append({"found":True,**classify_actual(row,item)})

    tiers=Counter(r["tier"] for r in rows)
    eligible=[r for r in rows if r["tier"] not in ("excluded","metadata_unavailable")]
    hidden=[r for r in eligible if r.get("matched_without_band_title")]
    report={
        "created_at":datetime.now(timezone.utc).isoformat(),
        "mode":"SHADOW ONLY: no Discord, no bookmarks, no paid AI",
        "ground_truth":"UNREVIEWED - no precision/recall claims",
        "tracked_channels":len(channels),
        "channels_scanned":len(channel_results),
        "channels_failed":len(failed_channels),
        "failed_channel_ids":sorted(failed_channels),
        "playlist_items_per_channel_limit":MAX_UPLOADS,
        "playlist_calls_attempted":len(channels),
        "video_metadata_calls":metadata_calls,
        "videos_from_playlists":len(all_rows),
        "metadata_unavailable":tiers["metadata_unavailable"],
        "excluded":tiers["excluded"],
        "eligible":len(eligible),
        "strong":tiers["strong"],"review":tiers["review"],
        "none":tiers["none"],
        "hidden_keyword_candidates":len(hidden),
        "candidate_examples":[
            {k:r.get(k) for k in ("video_id","title","channel","url","tier",
                                  "evidence","reasons","duration_sec")}
            for r in hidden[:60]
        ],
        "all_scanned":rows,
        "historical_controls":control_rows,
        "sampling_limitations":"Only latest 5 uploads of each tracked channel. "
             "Unreviewed classification; not a sensitivity/specificity benchmark. "
             "The same shared YouTube daily quota is used as production.",
    }
    return report


def main():
    key=os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise SystemExit("Missing YOUTUBE_API_KEY in isolated read-only test job")
    channels=json.loads(CHANNELS_PATH.read_text(encoding="utf-8")).get("channels",{})
    result=run(channels,key=key)
    OUTPUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print("LIVE KEYWORD SHADOW SUMMARY (never posts Discord)")
    for k in ("tracked_channels","channels_scanned","channels_failed",
              "videos_from_playlists","eligible","excluded","metadata_unavailable",
              "strong","review","none","hidden_keyword_candidates",
              "playlist_calls_attempted","video_metadata_calls"):
        print(f" {k}: {result[k]}")
    for row in result["candidate_examples"][:25]:
        print(f" [{row['tier'].upper()}] {row['video_id']} | {row['channel'][:30]} | "
              f"{row['title'][:80]} | {'; '.join(row['evidence'])}")
    print("REVIEW CANDIDATE DESCRIPTION EVIDENCE")
    for row in result["candidate_examples"]:
        if row.get("tier") == "review":
            full = next((v for v in result["all_scanned"]
                         if v.get("video_id") == row["video_id"]), {})
            print(f" review {row['video_id']} | description excerpt: "
                  f"{' '.join(full.get('description_excerpt','').split())[:1050]}")
    print("HISTORICAL CONTROL IDENTITIES")
    for row in result["historical_controls"]:
        print(f" control {row['video_id']} | found={row['found']} | "
              f"{row.get('tier','unavailable')} | {row.get('title','')[:90]}")
    print("This test does not estimate real-world precision or recall.")
    if result["channels_scanned"] == 0 or result["videos_from_playlists"] == 0:
        raise SystemExit("No live uploads fetched; controlled shadow trial invalid.")
    if result["channels_failed"] > len(channels)//4:
        raise SystemExit("More than 25% of channel scans failed; trial incomplete.")


if __name__=="__main__":
    main()
