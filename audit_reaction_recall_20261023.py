"""One-off, READ-ONLY reaction coverage audit. NEVER posts to Discord or edits bookmarks.

Costs: five search.list calls (500 units), ~one playlistItems.list per
tracked channel (1 unit each), and batched videos.list (1 unit each).
Only two example channels are allowed limited extra pagination.
"""
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from googleapiclient.discovery import build

os.environ["DRY_RUN"] = "true"
import autoreactions as tracker
from channel_exclusions import is_excluded_channel
from reaction_channel_policy import is_official_artist_channel
from video_release_policy import is_published_vod

SUBJECT = "missioned souls"
ROB = "UCRg2we57-t5Igtxh0_9SrPg"
TER = "UCju_HuzKeD3oBO_EYLvPNmA"
LOOKBACK_HOURS = 72
TARGETS = {
    ROB: ("robk", ("hate myself", "loving you")),
    TER: ("terrence", ("solo", "highway star"))
}
QUERIES = [
    ('broad_1', '"Missioned Souls"', 0),
    ('broad_2', '"Missioned Souls"', 1),
    ('reaction', '"Missioned Souls" reaction', 0),
    ('joan_jett', 'Missioned Souls "I Hate Myself For Loving You"', 0),
    ('highway_star', 'Missioned Souls "Highway Star"', 0),
]


def iso_datetime(value):
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d if d.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


def read_history():
    proc = subprocess.run(
        ["git", "show", "origin/notification-history:history/reactions.json"],
        capture_output=True, text=True, check=True,
    )
    return set(json.loads(proc.stdout)["sent_video_ids"])


def response_items(youtube, kind, *, kwargs):
    return getattr(youtube, kind)().list(**kwargs).execute()


def main():
    key = os.environ["YOUTUBE_API_KEY"]
    youtube = build("youtube", "v3", developerKey=key, cache_discovery=False)
    with open("tracked_channels.json", encoding="utf8") as file:
        tracked = json.load(file)["channels"]
    sent = read_history()
    now = datetime.now(timezone.utc)
    counters = defaultdict(int)
    videos = {}
    by_channel = defaultdict(list)
    errors = []
    matched_targets = defaultdict(list)
    approximate_units = 0

    for cid, info in tracked.items():
        if is_excluded_channel(cid) or is_official_artist_channel(cid):
            continue
        title = info["title"] if isinstance(info, dict) else str(info)
        token = None
        # All channels: first page only. Two examples: up to three further
        # pages if their specifically named reaction is not yet located.
        max_pages = 4 if cid in TARGETS else 1
        for page in range(max_pages):
            try:
                params = {"part": "snippet,contentDetails",
                          "playlistId": "UU"+cid[2:], "maxResults": 50}
                if token:
                    params["pageToken"] = token
                response = response_items(youtube, "playlistItems", kwargs=params)
                approximate_units += 1
            except Exception as exc:
                errors.append({"channel": title, "error_type": type(exc).__name__})
                break
            counters["playlist_pages"] += 1
            items = response.get("items", [])
            counters["uploads_examined"] += len(items)
            for pos, item in enumerate(items, start=page*50 + 1):
                sn = item.get("snippet", {})
                cd = item.get("contentDetails", {})
                vid = cd.get("videoId") or (sn.get("resourceId") or {}).get("videoId")
                if not vid:
                    continue
                title_text = sn.get("title", "")
                data = {
                    "video_id": vid, "channel_id": cid, "channel": title,
                    "title": title_text, "published_at": cd.get("videoPublishedAt")
                    or sn.get("publishedAt"), "position": pos,
                    "playlist_description": sn.get("description", ""),
                }
                videos[vid] = data
                by_channel[cid].append(data)
                if cid in TARGETS:
                    _, terms = TARGETS[cid]
                    low = title_text.lower()
                    if all(term in low for term in terms):
                        matched_targets[cid].append(data)
            token = response.get("nextPageToken")
            if cid not in TARGETS or matched_targets[cid] or not token:
                break

    # Search requests: five total; bounded, one-time only. Search is sampled
    # now and cannot prove what search returned during older scheduled runs.
    search_hits = defaultdict(set)
    broad_page_token = None
    for label, q, page in QUERIES:
        token = broad_page_token if page else None
        try:
            if page and not token:
                counters[label+"_missing_page"] += 1
                continue
            args = {"part":"snippet", "q":q, "type":"video",
                    "maxResults":50, "order":"date"}
            if token:
                args["pageToken"] = token
            result = response_items(youtube, "search", kwargs=args)
            approximate_units += 100
            if label == "broad_1":
                broad_page_token = result.get("nextPageToken")
            for item in result.get("items", []):
                vid = (item.get("id") or {}).get("videoId")
                if vid:
                    search_hits[label].add(vid)
            counters["search_hits_"+label] = len(search_hits[label])
        except Exception as exc:
            errors.append({"search": label, "error_type": type(exc).__name__})

    # Only metadata-verify recent plausible uploads plus the exact examples.
    inspect = []
    for data in videos.values():
        published = iso_datetime(data["published_at"])
        age = ((now - published).total_seconds()/3600) if published else 1e9
        title = data["title"]
        dsc = data["playlist_description"]
        plausible = (SUBJECT in title.lower() or
                     SUBJECT in dsc.lower() or
                     tracker.plausible_reaction_title(title))
        target = data["channel_id"] in TARGETS and data in matched_targets[data["channel_id"]]
        if (0 <= age <= LOOKBACK_HOURS and plausible) or target:
            data["age_h"] = round(age, 2)
            inspect.append(data)
    counters["metadata_candidates"] = len(inspect)
    details = {}
    for start in range(0, len(inspect), 50):
        ids = [v["video_id"] for v in inspect[start:start+50]]
        try:
            rsp = response_items(youtube, "videos", kwargs={
                "part": "snippet,contentDetails,status,liveStreamingDetails",
                "id": ",".join(ids),
            })
            approximate_units += 1
            details.update({item["id"]: item for item in rsp.get("items", [])})
        except Exception as exc:
            errors.append({"metadata_batch":start//50, "error_type":type(exc).__name__})

    eligible24 = []
    eligible72 = []
    rejected_subject = []
    for v in inspect:
        item = details.get(v["video_id"])
        if not item:
            continue
        snippet = item.get("snippet", {})
        duration = tracker.parse_duration((item.get("contentDetails") or {}).get("duration",""))
        confirmed = {
            **v,
            "title": snippet.get("title", v["title"]),
            "channel": snippet.get("channelTitle", v["channel"]),
            "description": snippet.get("description", ""),
            "duration_sec": duration,
            "live_broadcast_content": snippet.get("liveBroadcastContent","none"),
            "live_streaming_details": item.get("liveStreamingDetails"),
            "metadata_confirmed": True,
        }
        v["confirmed"] = {
            "title": confirmed["title"],
            "published_at": confirmed["published_at"],
            "age_h": v["age_h"],
            "duration_seconds":duration,
            "classified_reaction":tracker.is_safe_reaction_candidate(confirmed),
            "published_vod":is_published_vod(confirmed),
            "not_short":not tracker.is_short_or_too_short(confirmed),
            "fresh_expanded_match":tracker.recent_enough_for_first_expanded_match(confirmed,now=now),
            "delivered_history":v["video_id"] in sent
        }
        flags=v["confirmed"]
        if SUBJECT in confirmed["title"].lower() and not flags["classified_reaction"]:
            if 0 <= v["age_h"] <= LOOKBACK_HOURS:
                rejected_subject.append(v)
        if (flags["classified_reaction"] and flags["published_vod"] and
            flags["not_short"] and flags["fresh_expanded_match"] and
            0 <= v["age_h"] <= LOOKBACK_HOURS):
            eligible72.append(v)
            if v["age_h"] <= 24:
                eligible24.append(v)

    def metrics(rows):
        return {
            "found":len(rows),
            "already_delivered":sum(v["video_id"] in sent for v in rows),
            "not_in_confirmed_history":sum(v["video_id"] not in sent for v in rows),
            "currently_beyond_five":sum(v["position"]>5 for v in rows),
            "present_in_broad_search_snapshot":sum(v["video_id"] in
                (search_hits["broad_1"]|search_hits["broad_2"]) for v in rows),
            "present_in_any_search_snapshot":sum(v["video_id"] in
                set.union(set(), *search_hits.values()) for v in rows),
        }

    targets={}
    for cid, (name, terms) in TARGETS.items():
        matching=matched_targets[cid]
        targets[name]={
            "found_in_recent_upload_pages":bool(matching),
            "matching_uploads":[{
                "id":v["video_id"],"title":v["title"],"position":v["position"],
                **v.get("confirmed",{"details":"unverified"})
            } for v in matching],
            "channel_recorded_in_registry":cid in tracked,
            "latest_upload_count_in_audit":len(by_channel[cid]),
        }

    # Missing means NOT in delivery history at this instant, not a guaranteed
    # failure to notify. Some entries can still be pending or too newly posted.
    result={
        "audit_at_utc":now.isoformat(),
        "scope":"127 tracked channels, newest 50 uploads each, last 72h for candidate classification; up to 200 uploads for two examples",
        "history_count":len(sent),
        "tracked_count":len(tracked),
        "channels_examined":len(by_channel),
        "uploads_examined":counters["uploads_examined"],
        "metadata_candidates":len(inspect),
        "verified_eligible_last_24h":metrics(eligible24),
        "verified_eligible_last_72h":metrics(eligible72),
        "rejected_subject_title_last_72h_count":len(rejected_subject),
        "rejected_subject_title_examples":[{
            "video_id":v["video_id"],"channel":v["channel"],
            "title":v["title"],"age_h":v["age_h"],
            "position":v["position"]
        } for v in rejected_subject[:20]],
        "eligible_but_not_in_history_last_24h":[{
            "video_id":v["video_id"],"channel":v["channel"],
            "title":v["title"],"age_h":v["age_h"],
            "position":v["position"]
        } for v in eligible24 if v["video_id"] not in sent][:35],
        "search_results":{k:len(v) for k,v in search_hits.items()},
        "specified_examples":targets,
        "estimated_api_units_for_this_single_audit":approximate_units,
        "api_errors":errors[:15],
    }
    print("AUDIT_JSON_START")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("AUDIT_JSON_END")


if __name__ == "__main__":
    main()
