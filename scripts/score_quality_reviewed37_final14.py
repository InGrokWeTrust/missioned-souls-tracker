"""Read-only final human-review overlay of the same 37 selected Missioned Souls videos.

Source snapshots, preceding audit output and every submitted label remain
immutable. In particular: TWO human reactions had prior metadata-only
"exclude" verdicts; report them as potential false negatives, not as
automatic permission to change the classifier or production Discord policy.

No network, YouTube, Discord, webhook, or production bookmark interactions.
"""
from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path

from scripts.score_quality_reviewed37 import ROOT, _csv
from scripts.score_quality_reviewed37_followup import evaluate_followup

PENDING = ROOT / "data" / "reaction_quality_remaining14_pending_review_20261022.json"
HUMAN = ROOT / "data" / "reaction_quality_remaining14_human_labels_20261022.csv"
OUTPUT = ROOT / "reaction-quality-reviewed37-with-final14.json"

# Exact user-submitted verdicts are frozen; no inferred corrections.
EXPECTED_REACTIONS = frozenset({
    "ETb_O74yn20", "X2iIie9DGMQ", "emohzErkSxU",
    "4Irh5GOuklU", "zPZf_TsXeak", "qz3tZGs8nzo",
    "eg1mmc5N4_M", "qHOVcqgRRgA", "y2CRFwhqSqM",
    "wf86HLqLmN8", "8PMWuRU-H30", "XBCg8-44GJs",
    "IjaXJDLaIEM", "qqhUL5a0rik",
})
PRIOR_METADATA_EXCLUSIONS = frozenset({"IjaXJDLaIEM", "qqhUL5a0rik"})
METADATA_STATUS = {
    "supported_reaction": "metadata_supported_reaction",
    "exclude_by_reactions_only_policy": "metadata_assessed_nonreaction",
}


def apply_final_review(previous, pending, human_rows):
    """Return new report; validate IDs, uploader, original verdict and provenance."""
    if previous.get("total_selected_band_related") != 37:
        raise ValueError("Unexpected old audit size")
    old_rows = previous.get("cases", [])
    old_by = {r["video_id"]: r for r in old_rows}
    if len(old_rows) != 37 or len(old_by) != 37:
        raise ValueError("Old report IDs invalid")

    selected = pending.get("items", [])
    by_selected = {r["video_id"]: r for r in selected}
    by_human = {r["video_id"]: r for r in human_rows}
    if (len(selected) != 14 or len(by_selected) != 14
            or set(by_selected) != EXPECTED_REACTIONS):
        raise ValueError("Final frozen selection must contain exactly 14 expected IDs")
    if (len(human_rows) != 14 or len(by_human) != 14
            or set(by_human) != set(by_selected)):
        raise ValueError("Human review must include exactly 14 distinct selected IDs")
    if pending.get("youtube_api_calls") != 0 or pending.get("discord_posts") != 0:
        raise ValueError("Review source must not authorise live operations")

    for vid, row in by_selected.items():
        if row.get("review_label") is not None or row.get("reviewer") is not None:
            raise ValueError("Do not change original blank reviewer queue")
        if vid not in old_by or row["channel_id"] != old_by[vid]["channel_id"]:
            raise ValueError("Review ID/uploader differs from earlier evidence")
        if METADATA_STATUS.get(row.get("prior_metadata_status")) != old_by[vid]["truth_provenance"]:
            raise ValueError("Original metadata provenance differs from frozen audit")
        submitted = by_human[vid]
        if (submitted.get("review_label") != "reaction"
                or submitted.get("reviewer") != "human"
                or not isinstance(submitted.get("notes"), str)
                or submitted.get("notes") != ""):
            raise ValueError("Human review must be exactly as submitted")
        if (vid in PRIOR_METADATA_EXCLUSIONS) != (
                row["prior_metadata_status"] == "exclude_by_reactions_only_policy"):
            raise ValueError("Metadata disagreement IDs do not match frozen source")

    result = copy.deepcopy(previous)
    updated = []
    disagreements = []
    for row in result["cases"]:
        vid = row["video_id"]
        if vid not in by_selected:
            continue
        previous_provenance = row["truth_provenance"]
        row["truth_provenance"] = "verified_reaction"
        row["previous_metadata_provenance"] = previous_provenance
        row["human_label_source"] = "data/reaction_quality_remaining14_human_labels_20261022.csv"
        row["human_followup_reviewed"] = True
        row["original_classifier_tier_retained"] = row["classifier_tier"]
        disagreement = previous_provenance == "metadata_assessed_nonreaction"
        row["human_metadata_disagreement"] = disagreement
        updated.append({
            "video_id": vid,
            "human_verdict": "reaction",
            "original_metadata_provenance": previous_provenance,
            "classifier_tier_unchanged": row["classifier_tier"],
            "needs_policy_review": disagreement,
        })
        if disagreement:
            disagreements.append({
                "video_id":vid,
                "human_verdict":"reaction",
                "prior_metadata_status":"exclude_by_reactions_only_policy",
                "existing_classifier_tier":row["classifier_tier"],
                "remedy":"review exclusion false-negative risk; no automatic code or Discord change",
            })
    if len(updated) != 14 or {r["video_id"] for r in disagreements} != PRIOR_METADATA_EXCLUSIONS:
        raise ValueError("Final review overlay or discrepancy list incomplete")

    counts = Counter(row["truth_provenance"] for row in result["cases"])
    reaction_tiers = Counter(row["classifier_tier"] for row in result["cases"]
                             if row["truth_provenance"] == "verified_reaction")
    nonreaction_tiers = Counter(row["classifier_tier"] for row in result["cases"]
                                if row["truth_provenance"] == "verified_nonreaction")
    if counts != {"verified_reaction":28, "verified_nonreaction":9}:
        raise ValueError("Unexpected 37-case final reviewed totals")

    result.update({
        "source":"Same 37 selected videos, now all 37 human-reviewed via prior evidence and 14 user-submitted verdicts",
        "confirmed_reactions":28,
        "confirmed_nonreactions":9,
        "metadata_supported_reactions_not_video_verified":0,
        "metadata_assessed_nonreactions_not_video_verified":0,
        "human_reaction_tiers":{x:reaction_tiers[x] for x in ("strong","review","none")},
        "human_nonreaction_tiers":{x:nonreaction_tiers[x] for x in ("strong","review","none")},
        "final_fourteen_verdicts_received":True,
        "final_fourteen_verdicts":sorted(updated,key=lambda r:r["video_id"]),
        "human_metadata_disagreements":sorted(disagreements,key=lambda r:r["video_id"]),
        "human_metadata_disagreement_count":len(disagreements),
        "baseline_and_pending_sources_unchanged":True,
        "no_population_metrics":True,
        "no_discord_authorization":True,
        "reviewer":"User-supplied video-level human judgments; assistant did not independently watch",
        "selection_warning":"Intentionally selected 37-case sample; previous eight and these final fourteen are from the same original thirty. Do not extrapolate performance. Two title-based prior nonreaction exclusions conflict with user video-level reaction verdicts; human truth remains separate from classifier and requires explicit policy review.",
    })
    return result


def evaluate_final():
    return apply_final_review(
        evaluate_followup(),
        json.loads(PENDING.read_text(encoding="utf-8")),
        _csv(HUMAN),
    )


def main():
    report = evaluate_final()
    OUTPUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print("FINAL 14 HUMAN VERDICTS — SAME 37 VIDEOS, ZERO NETWORK CALLS")
    for name in ("total_selected_band_related","confirmed_reactions",
                 "confirmed_nonreactions","metadata_supported_reactions_not_video_verified",
                 "metadata_assessed_nonreactions_not_video_verified",
                 "human_reaction_tiers","human_nonreaction_tiers",
                 "human_metadata_disagreement_count"):
        print(f" {name}: {report[name]}")
    for item in report["human_metadata_disagreements"]:
        print(f" NEEDS RULE REVIEW: {item['video_id']} human=reaction prior_metadata=exclude; "
              f"classifier={item['existing_classifier_tier']}")
    print("No production policy changes, no Discord sending, no YouTube API requests.")


if __name__ == "__main__":
    main()
