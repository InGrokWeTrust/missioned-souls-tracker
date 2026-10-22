"""Offline per-channel Missioned Souls reaction history for 125 tracked reactors.

Uses only frozen historical video-ID/channel-ID mapping and user-reviewed
labels. NEVER estimates channel trust from the share of unrelated uploads.
One confirmed reaction is history evidence, NOT a reliable auto-post source.
No APIs, Discord, production state writes, or automatic promotions.
"""
from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REGISTRY=ROOT/"tracked_channels.json"
MAP=ROOT/"data"/"reactor_history_video_channels_20261022.json"
STRONG=ROOT/"data"/"keyword_strong30_reactions_only_adjudication_20261022.json"
FIVE=ROOT/"data"/"keyword_reactions_only_five_human_labels_20261022.csv"
EARLIER=ROOT/"data"/"keyword_manual_labels_20261022.csv"
OUTPUT=ROOT/"reactor-history-report.json"

# The user individually confirmed the original Nitro Neisha video was a
# reaction, and explicitly described the tribute and Paper Dove as invalid.
USER_CONFIRMED_STRONG={"hf03Voz2QBM":"reaction",
                       "fqTvzUxJT1k":"nonreaction",
                       "r5TVQYSmZcY":"nonreaction"}
EXPECTED_HUMAN_EARLIER={"jxoHvBlRABA","E2tRH0B6Ow8"}
STATUS_CHOICES={
    "verified_reaction", "verified_band_nonreaction",
    "metadata_supported_reaction", "metadata_nonreaction",
}


def _csv(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build_cases(adjudication, five, earlier):
    if len(five)!=5 or len(adjudication.get("items", []))!=30:
        raise ValueError("Frozen 30-video review or five-video follow-up is incomplete")
    five_map={}
    for row in five:
        if row["reviewer"]!="human" or row["content_type"] not in ("reaction","repost","discussion"):
            raise ValueError("Unverified five-video content assessment")
        if row["video_id"] in five_map: raise ValueError("Duplicate follow-up video ID")
        five_map[row["video_id"]]=row["content_type"]
    expected=set(("VhwqMoL-t8A","cx1s0V_8Bu4","JjFzKrfhNgg",
                  "iVeWY7mTkrE","gXWQQNUpKcA"))
    if set(five_map)!=expected: raise ValueError("Unexpected human follow-up ID set")
    cases={}
    for item in adjudication["items"]:
        vid=item["video_id"]
        if vid in cases: raise ValueError("Duplicate band video in frozen sample")
        if vid in five_map:
            if item["status"]!="recheck_video":
                raise ValueError("Follow-up cannot overwrite a resolved video")
            status=("verified_reaction" if five_map[vid]=="reaction"
                    else "verified_band_nonreaction")
        elif vid in USER_CONFIRMED_STRONG:
            status=("verified_reaction"
                    if USER_CONFIRMED_STRONG[vid]=="reaction"
                    else "verified_band_nonreaction")
        elif item["status"]=="supported_reaction":
            status="metadata_supported_reaction"
        elif item["status"]=="exclude_by_reactions_only_policy":
            status="metadata_nonreaction"
        else:
            raise ValueError("Unresolved band video is missing human follow-up")
        cases[vid]={"video_id":vid,"status":status,"source":"strong30_review"}
    earlier_pos=set()
    for row in earlier:
        if row["review_label"]=="match":
            if row["reviewer"]!="human":raise ValueError("Earlier positive must be human-reviewed")
            earlier_pos.add(row["video_id"])
    if earlier_pos!=EXPECTED_HUMAN_EARLIER:raise ValueError("Unrecognised earlier reviewed reaction positives")
    for vid in earlier_pos:
        if vid in cases:raise ValueError("Duplicate video across review sets")
        cases[vid]={"video_id":vid,"status":"verified_reaction","source":"46_video_review"}
    if len(cases)!=32:raise ValueError("Expected exactly 32 band-relevant labelled videos")
    return cases


def assess_all(registry, mapping, cases, *, expected_verified_reactions=6, expected_verified_nonreactions=4):
    if not isinstance(registry,dict) or len(registry)!=125:
        raise ValueError("Expected full frozen set of 125 tracked reactors")
    ids=mapping.get("video_channel_ids",{})
    if set(ids)!=set(cases):
        raise ValueError("Band-specific labelled videos and frozen YouTube channel IDs disagree")
    for vid,cid in ids.items():
        if cid not in registry:raise ValueError("Reviewed video belongs to untracked channel: "+vid)
    channels=[]
    for cid,entry in registry.items():
        label=entry.get("title",cid) if isinstance(entry,dict) else str(entry)
        evidence={s:[] for s in STATUS_CHOICES}
        for vid,data in cases.items():
            if ids[vid]==cid:evidence[data["status"]].append(vid)
        for k in evidence:evidence[k].sort()
        yes=len(evidence["verified_reaction"])
        no=len(evidence["verified_band_nonreaction"])
        maybe=len(evidence["metadata_supported_reaction"])
        if yes and no:
            history_signal="mixed_confirmed_content"
        elif yes==1:
            history_signal="one_confirmed_reaction"
        elif yes>=2:
            history_signal="multiple_confirmed_reactions"
        elif maybe:
            history_signal="only_provisional_metadata"
        elif no:
            history_signal="confirmed_nonreaction_only"
        else:
            history_signal="insufficient_band_specific_review"
        # No current evidence for channel-level auto posting; require independent
        # verified genuine reactions on multiple dates, which we don't have.
        channels.append({
            "channel_id":cid,"name":label,
            "history_signal":history_signal,
            "confirmed_reactions":yes,
            "confirmed_band_nonreactions":no,
            "metadata_supported_reaction_candidates":maybe,
            "metadata_assessed_nonreactions":len(evidence["metadata_nonreaction"]),
            "reviewed_band_related_video_ids":evidence,
            "eligible_for_auto_approval":False,
            "source_history_affects_only_manual_review":True,
        })
    counts=Counter(c["history_signal"] for c in channels)
    verified=sum(c["confirmed_reactions"] for c in channels)
    verified_non=sum(c["confirmed_band_nonreactions"] for c in channels)
    if verified!=expected_verified_reactions or verified_non!=expected_verified_nonreactions:
        raise ValueError("Historical human evidence totals changed")
    return {
        "policy":"Third-party Missioned Souls performance reactions only",
        "as_of":"2026-10-22",
        "channels_total":len(channels),
        "band_relevant_videos_assessed":len(cases),
        "verified_reaction_videos":verified,
        "verified_band_nonreaction_videos":verified_non,
        "metadata_supported_reaction_candidates":sum(c["metadata_supported_reaction_candidates"] for c in channels),
        "metadata_assessed_nonreaction_videos":sum(c["metadata_assessed_nonreactions"] for c in channels),
        "earlier_unrelated_human_negative_controls_excluded_from_channel_trust":44,
        "channels_with_at_least_one_verified_reaction":sum(c["confirmed_reactions"]>0 for c in channels),
        "channels_with_two_or_more_verified_reactions":sum(c["confirmed_reactions"]>=2 for c in channels),
        "mixed_verified_content_channels":sum(c["confirmed_reactions"]>0 and c["confirmed_band_nonreactions"]>0 for c in channels),
        "channels_by_signal":dict(sorted(counts.items())),
        "no_auto_approval":True,
        "scope_limit":"Only 32 relevant cases drawn from two deliberately sampled review sets. "
                      "The other 44 reviewed negatives were unrelated uploads and MUST NOT "
                      "lower a channel's rate of producing genuine Missioned Souls reactions. "
                      "Unlabelled keyword matches are NOT verified positives. This is not "
                      "a complete channel history or a true channel reliability rate.",
        "next_gate":"Collect at least 3 independent human-confirmed reaction IDs per channel "
                    "over more than one date, while tracking human-confirmed BAND-related "
                    "false positives; use history to prioritize manual review only. "
                    "Never auto-send purely from uploader history.",
        "channels":channels,
    }


def main():
    registry=json.loads(REGISTRY.read_text(encoding="utf-8"))["channels"]
    mapping=json.loads(MAP.read_text(encoding="utf-8"))
    prior=json.loads(STRONG.read_text(encoding="utf-8"))
    cases=build_cases(prior,_csv(FIVE),_csv(EARLIER))
    result=assess_all(registry,mapping,cases)
    OUTPUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print("HUMAN REVIEWED REACTOR HISTORY — REPORT ONLY")
    for k in ("channels_total","band_relevant_videos_assessed",
              "verified_reaction_videos","verified_band_nonreaction_videos",
              "metadata_supported_reaction_candidates",
              "metadata_assessed_nonreaction_videos",
              "channels_with_at_least_one_verified_reaction",
              "channels_with_two_or_more_verified_reactions",
              "mixed_verified_content_channels"):
        print(f" {k}: {result[k]}")
    for row in result["channels"]:
        if row["confirmed_reactions"] or row["confirmed_band_nonreactions"]:
            print(f" {row['name'][:38]} | reactions={row['confirmed_reactions']} "
                  f"band-nonreactions={row['confirmed_band_nonreactions']} | "
                  f"{row['history_signal']}")
    print("Never approve channels for Discord automatically from this evidence.")


if __name__=="__main__":
    main()
