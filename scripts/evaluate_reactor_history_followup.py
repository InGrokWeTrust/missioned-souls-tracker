"""Apply seven user-verified Missioned Souls content labels to reactor history.

A channel's band-specific reaction evidence is based on individually
human-reviewed video IDs. No live API, no cost, no Discord, no state writes.
HCJB is excluded from experimental reaction discovery by explicit user
policy, but its previously confirmed true reaction remains in historical data.
"""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
from scripts.evaluate_reactor_history import (
    ROOT, REGISTRY, MAP, STRONG, FIVE, EARLIER, _csv,
    build_cases, assess_all,
)
from keyword_detector import EXCLUDED_REACTOR_CHANNEL_IDS

PENDING=ROOT/"data"/"reactor_history_next7_pending_review_20261022.json"
LABELS=ROOT/"data"/"reactor_history_next7_human_labels_20261022.csv"
OUTPUT=ROOT/"reactor-history-followup-report.json"
HCJB="UCtEs-cB4K1XFnXgLrIWW1Zg"

def merge_review(prior, mapping, pending, reviews):
    cases=dict(prior)
    mapping=dict(mapping)
    video_channels=dict(mapping["video_channel_ids"])
    pending_rows=pending["items"]
    pending_by={item["video_id"]:item for item in pending_rows}
    if len(pending_rows)!=7 or len(pending_by)!=7 or len(reviews)!=7:
        raise ValueError("Expected precisely 7 unique review candidates and labels")
    if any(item.get("review_label") is not None for item in pending_rows):
        raise ValueError("Pending selection was prelabelled")
    labels={}
    for row in reviews:
        vid=row["video_id"]
        if (row.get("reviewer")!="human" or
                row.get("review_label") not in ("reaction","not_match") or
                vid in labels):
            raise ValueError("Invalid, duplicate or nonhuman verdict")
        labels[vid]=row
    if set(labels)!=set(pending_by):
        raise ValueError("Human verdict video IDs differ from frozen 7-item selection")
    for vid, item in pending_by.items():
        if vid in cases or vid in video_channels:
            raise ValueError("Attempt to relabel already audited historical video")
        video_channels[vid]=item["channel_id"]
        cases[vid]={
            "video_id":vid,
            "source":"seven_video_user_review",
            "status":("verified_reaction" if labels[vid]["review_label"]=="reaction"
                      else "verified_band_nonreaction"),
        }
    mapping["video_channel_ids"]=video_channels
    return mapping,cases

def evaluate():
    reg=json.loads(REGISTRY.read_text(encoding="utf-8"))["channels"]
    prior=build_cases(json.loads(STRONG.read_text(encoding="utf-8")),
                      _csv(FIVE),_csv(EARLIER))
    mapping,cases=merge_review(
        prior, json.loads(MAP.read_text(encoding="utf-8")),
        json.loads(PENDING.read_text(encoding="utf-8")),_csv(LABELS))
    result=assess_all(reg,mapping,cases,
                      expected_verified_reactions=10,
                      expected_verified_nonreactions=7)
    counts=Counter(x["status"] for x in cases.values())
    result["human_followup_videos"]=7
    result["prior_band_relevant_videos"]=32
    result["excluded_reactor_ids_in_experimental_classifier"]=sorted(EXCLUDED_REACTOR_CHANNEL_IDS)
    result["all_history_kept_even_for_user_excluded_channels"]=True
    result["sample_warning"]="Not random channel history: seven videos selected from previously identified channels; observed ratios are sample-specific, not reliable channel-wide rates."
    for channel in result["channels"]:
        channel["user_excluded_from_experimental_reaction_discovery"]=(
            channel["channel_id"] in EXCLUDED_REACTOR_CHANNEL_IDS)
        channel["eligible_for_auto_approval"]=False
    if HCJB not in EXCLUDED_REACTOR_CHANNEL_IDS:
        raise ValueError("Expected HCJB user exclusion absent")
    by={c["channel_id"]:c for c in result["channels"]}
    if (by[HCJB]["confirmed_reactions"]!=1 or
        by[HCJB]["confirmed_band_nonreactions"]!=4):
        raise ValueError("HCJB human-reviewed evidence disagrees with frozen results")
    if result["band_relevant_videos_assessed"]!=39:
        raise ValueError("Expected 39 distinct band-related entries")
    result["verified_case_counts"]=dict(sorted(counts.items()))
    return result

def main():
    result=evaluate()
    OUTPUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print("FOLLOW-UP REACTOR HISTORY — OFFLINE NO DISCORD")
    for k in ("channels_total","band_relevant_videos_assessed",
              "verified_reaction_videos","verified_band_nonreaction_videos",
              "channels_with_at_least_one_verified_reaction",
              "channels_with_two_or_more_verified_reactions"):
        print(f" {k}: {result[k]}")
    for cid in (HCJB,"UCahJtq3gSn-QXXTGzTaj7Fg","UC8ReQVU4PPfbxRy4B18jauw"):
        c=next(x for x in result["channels"] if x["channel_id"]==cid)
        print(f" {c['name']}: {c['confirmed_reactions']} reactions, "
              f"{c['confirmed_band_nonreactions']} band-related nonreactions, "
              f"user_excluded={c['user_excluded_from_experimental_reaction_discovery']}")
    print("All production and experimental automatic keyword delivery remains unchanged/disabled.")

if __name__=="__main__":
    main()
