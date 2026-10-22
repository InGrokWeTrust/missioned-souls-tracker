"""Re-score the frozen strong30 sample for genuine reactions ONLY.

Never overwrite original user labels. Two independent layers:
1. frozen human labels under earlier broader interpretation;
2. explicit reactions-only policy adjudication based on title, notes, and
   video-specific public descriptions, WITHOUT new human video review.

No API calls, credentials, Discord, or bookmark writes.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from keyword_detector import classify
from scripts.score_keyword_strong30 import load_labels, LABELS, SELECTION

ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION = ROOT / "data" / "keyword_strong30_reactions_only_adjudication_20261022.json"
OUTPUT = ROOT / "keyword-reactions-only-results.json"
STATUSES = {
    "supported_reaction",
    "exclude_by_reactions_only_policy",
    "recheck_video",
}


def evaluate(adjudication, selection, raw_labels):
    ids = selection.get("video_ids_in_review_order", [])
    items = adjudication.get("items", [])
    if len(ids) != 30 or len(items) != 30 or len(raw_labels) != 30:
        raise ValueError("Reactions-only evaluation requires 30 original reviews")
    if ([i.get("video_id") for i in items] != ids
            or [x["video_id"] for x in raw_labels] != ids
            or len(set(ids)) != 30):
        raise ValueError("Adjudication and raw labels must agree exactly with frozen IDs")
    results = []
    for item, label in zip(items, raw_labels):
        if item.get("status") not in STATUSES:
            raise ValueError("Invalid or unconfirmed adjudication status")
        if not isinstance(item.get("rationale"), str) or len(item["rationale"]) < 15:
            raise ValueError("Missing independent evidence/rationale")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            raise ValueError("Missing frozen title")
        channel = ("MISSIONED SOULS REACTION ARCHIVE"
                   if item["video_id"] == "VhwqMoL-t8A" else "")
        classification = classify(item["title"], channel_name=channel)
        # A high-confidence title signal must never be emitted for something
        # known to be outside reactions-only scope or requiring video review.
        if (item["status"] != "supported_reaction"
                and classification["tier"] == "strong"):
            raise ValueError(f"Unsafe high-confidence signal: {item['video_id']}")
        results.append({
            "video_id":item["video_id"],
            "title":item["title"],
            "raw_user_label":label["review_label"],
            "raw_user_note":label["notes"],
            "reactions_only_assessment":item["status"],
            "assessment_basis":item["rationale"],
            "new_keyword_tier":classification["tier"],
            "would_auto_post":False,
            "verified_by_new_video_watch":False,
        })
    statuses = Counter(x["reactions_only_assessment"] for x in results)
    tiers = Counter(x["new_keyword_tier"] for x in results)
    return {
        "policy":"STRICT third-party reaction/critique/analysis of a Missioned Souls musical performance",
        "total":len(results),
        "metadata_supported_reaction_count":statuses["supported_reaction"],
        "metadata_excluded_nonreaction_count":statuses["exclude_by_reactions_only_policy"],
        "requires_video_recheck_count":statuses["recheck_video"],
        "classifications_after_new_rules":dict(tiers),
        "original_user_match_count":sum(x["raw_user_label"]=="match" for x in results),
        "original_user_not_match_count":sum(x["raw_user_label"]=="not_match" for x in results),
        "population_precision":None,
        "population_recall":None,
        "new_human_verified_ground_truth":False,
        "sampling_limitations":"Original sample purposefully risk-enriched and channel-balanced; "
                              "provisional metadata categories are NOT a fresh human-video re-review.",
        "deployment_safety":"Experimental only. Keyword-triggered Discord posting remains disabled.",
        "cases":results,
    }


def main():
    policy=json.loads(ADJUDICATION.read_text(encoding="utf-8"))
    selection=json.loads(SELECTION.read_text(encoding="utf-8"))
    labels=load_labels(LABELS)
    r=evaluate(policy,selection,labels)
    OUTPUT.write_text(json.dumps(r,indent=2,ensure_ascii=False),encoding="utf-8")
    print("REACTIONS-ONLY POLICY REASSESSMENT — SHADOW ONLY")
    for k in ("total","metadata_supported_reaction_count",
              "metadata_excluded_nonreaction_count","requires_video_recheck_count",
              "classifications_after_new_rules","original_user_match_count",
              "original_user_not_match_count"):
        print(f" {k}: {r[k]}")
    print("Unresolved video IDs:",
          ", ".join(x["video_id"] for x in r["cases"]
                    if x["reactions_only_assessment"] == "recheck_video"))
    print("No global performance claim; source human labels remain unmodified.")


if __name__ == "__main__":
    main()
