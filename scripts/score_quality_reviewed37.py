"""Offline evidence audit of 37 band-related videos, with explicit label provenance.

Risk-enriched human review sets: 30 older + 7 new. Human-confirmed
performance reactions and nonreactions remain separate from metadata-only
assessments. No API, Discord, bookmarks, or population accuracy claims.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from keyword_detector import classify

ROOT=Path(__file__).resolve().parents[1]
ADJ=ROOT/"data"/"keyword_strong30_reactions_only_adjudication_20261022.json"
FIVE=ROOT/"data"/"keyword_reactions_only_five_human_labels_20261022.csv"
MAPPING=ROOT/"data"/"reactor_history_video_channels_20261022.json"
SEVEN_CASES=ROOT/"data"/"reactor_history_next7_pending_review_20261022.json"
SEVEN_LABELS=ROOT/"data"/"reactor_history_next7_human_labels_20261022.csv"
CHANNELS=ROOT/"tracked_channels.json"
OUTPUT=ROOT/"reaction-quality-reviewed37.json"

# The user had already directly verified these before the 5-video follow-up.
PRIOR_HUMAN={"hf03Voz2QBM":"reaction","fqTvzUxJT1k":"not_match",
             "r5TVQYSmZcY":"not_match"}
HUMAN_POS={"reaction"}
HUMAN_NEG={"not_match","repost","discussion"}
METADATA={"supported_reaction":"metadata_supported_reaction",
          "exclude_by_reactions_only_policy":"metadata_assessed_nonreaction"}


def _csv(path):
    with Path(path).open(encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))


def build_rows(adjudication, five, mapping, seven, seven_labels):
    if len(adjudication["items"])!=30 or len(five)!=5 or len(seven["items"])!=7 or len(seven_labels)!=7:
        raise ValueError("Historical review cohorts are missing cases")
    five_by={r["video_id"]:r for r in five}
    seven_by={r["video_id"]:r for r in seven_labels}
    if (len(five_by)!=5 or len(seven_by)!=7 or
            set(five_by)!={"VhwqMoL-t8A","cx1s0V_8Bu4","JjFzKrfhNgg",
                           "iVeWY7mTkrE","gXWQQNUpKcA"} or
            set(seven_by)!={r["video_id"] for r in seven["items"]}):
        raise ValueError("Human follow-up IDs do not match frozen candidates")
    ids=mapping["video_channel_ids"]
    rows=[]
    used=set()
    for item in adjudication["items"]:
        vid=item["video_id"]
        if vid in used or vid not in ids:raise ValueError("Duplicate/unmapped old video")
        used.add(vid)
        if vid in five_by:
            if five_by[vid]["reviewer"]!="human" or item["status"]!="recheck_video":
                raise ValueError("Unverified follow-up overwrote old judgment")
            outcome=five_by[vid]["content_type"]
            if outcome not in HUMAN_POS|HUMAN_NEG:raise ValueError("Invalid human label")
            label=("verified_reaction" if outcome in HUMAN_POS else "verified_nonreaction")
        elif vid in PRIOR_HUMAN:
            label=("verified_reaction" if PRIOR_HUMAN[vid]=="reaction"
                   else "verified_nonreaction")
        else:
            label=METADATA.get(item["status"])
            if label is None:raise ValueError("Unresolved video must get human review")
        rows.append({"video_id":vid,"title":item["title"],"channel_id":ids[vid],
                     "truth_provenance":label,"cohort":"30_selected"})
    for item in seven["items"]:
        vid=item["video_id"]
        if vid in used:raise ValueError("Repeated video across cohorts")
        used.add(vid)
        human=seven_by[vid]
        if human["reviewer"]!="human" or human["review_label"] not in HUMAN_POS|{"not_match"}:
            raise ValueError("Invalid seven-case human verdict")
        rows.append({"video_id":vid,"title":item["title"],"channel_id":item["channel_id"],
                     "truth_provenance":("verified_reaction"
                        if human["review_label"]=="reaction" else "verified_nonreaction"),
                     "cohort":"7_selected"})
    if len(rows)!=37 or len(used)!=37:
        raise ValueError("Expected 37 distinct selected human/metadata cases")
    return rows


def evaluate():
    registry=json.loads(CHANNELS.read_text(encoding="utf-8"))["channels"]
    rows=build_rows(json.loads(ADJ.read_text(encoding="utf-8")),
                    _csv(FIVE),json.loads(MAPPING.read_text(encoding="utf-8")),
                    json.loads(SEVEN_CASES.read_text(encoding="utf-8")),_csv(SEVEN_LABELS))
    counts=Counter()
    evidence=[]
    for row in rows:
        cid=row["channel_id"]
        tracked=cid in registry
        result=classify(row["title"],channel_id=cid,
                        channel_name=registry.get(cid,{}).get("title",""),
                        tracked_reactor=tracked,
                        verified_reactor_source=tracked)
        label=row["truth_provenance"]
        counts[label]+=1
        counts["tier_"+result["tier"]]+=1
        if label=="verified_reaction":
            counts["human_reactions_"+result["tier"]]+=1
        elif label=="verified_nonreaction":
            counts["human_nonreactions_"+result["tier"]]+=1
        evidence.append({
            "video_id":row["video_id"],"channel_id":cid,"cohort":row["cohort"],
            "truth_provenance":label,"classifier_tier":result["tier"],
            "review_priority":result["review_priority"],
            "auto_candidate_is_not_send_permission":result["auto_candidate"],
        })
    # A directly rejected example may remain review-tier but must never
    # be strong enough to send automatically without its human rejection.
    if counts["human_nonreactions_strong"]:
        raise ValueError("Human-confirmed nonreaction promoted to strong evidence")
    if counts["human_reactions_none"]:
        raise ValueError("Human-confirmed reaction completely hidden by classifier")
    return {
        "source":"Two targeted, risk-enriched 2026-10-22 cohorts (not random)",
        "total_selected_band_related":len(evidence),
        "confirmed_reactions":counts["verified_reaction"],
        "confirmed_nonreactions":counts["verified_nonreaction"],
        "metadata_supported_reactions_not_video_verified":counts["metadata_supported_reaction"],
        "metadata_assessed_nonreactions_not_video_verified":counts["metadata_assessed_nonreaction"],
        "human_reaction_tiers":{t:counts["human_reactions_"+t] for t in ("strong","review","none")},
        "human_nonreaction_tiers":{t:counts["human_nonreactions_"+t] for t in ("strong","review","none")},
        "no_population_metrics":True,
        "no_discord_authorization":True,
        "selection_warning":"Older 30 sampled by risk/channel and seven manually selected for channel histories. "
                            "Do NOT extrapolate sample ratios as channel or population precision/recall. "
                            "Descriptions, tags and original video footage were NOT re-examined by this run.",
        "cases":evidence,
    }


def main():
    result=evaluate()
    OUTPUT.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print("REACTION QUALITY HUMAN-METADATA SEPARATED — OFFLINE")
    for key in ("total_selected_band_related","confirmed_reactions",
                "confirmed_nonreactions","metadata_supported_reactions_not_video_verified",
                "metadata_assessed_nonreactions_not_video_verified",
                "human_reaction_tiers","human_nonreaction_tiers"):
        print(f" {key}: {result[key]}")
    print("No Discord posts or YouTube API requests; no population precision/recall estimated.")


if __name__=="__main__":
    main()
