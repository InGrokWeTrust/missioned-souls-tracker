"""Re-score the same 37 selected videos after eight independent human verdicts.

The original metadata adjudication, 37-video baseline, and pending queue
remain immutable. A separate human CSV overlays EXACT video IDs and yields
a distinct follow-up report. Zero YouTube/API/Discord/state interactions.
Do not interpret deliberately selected samples as population metrics.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from scripts.score_quality_reviewed37 import ROOT, evaluate as baseline_evaluate, _csv

PENDING=ROOT/"data"/"reaction_quality_next8_pending_review_20261022.json"
HUMAN=ROOT/"data"/"reaction_quality_next8_human_labels_20261022.csv"
OUTPUT=ROOT/"reaction-quality-reviewed37-with-next8.json"

EXPECTED_HUMAN={
    "Yrt40Jti9zA":"reaction",
    "WZpAnBgf_aM":"reaction",
    "rhoHMkgcvj0":"reaction",
    "_SBmrJZBaaE":"reaction",
    "QJp3kNQEUG0":"reaction",
    "XA1m863syWM":"reaction",
    "6Eu4f5oqXnk":"not_match",
    "vpYHLQr04LQ":"not_match",
}
SOURCE_ALLOWED={
    "reaction":"metadata_supported_reaction",
    "not_match":"metadata_assessed_nonreaction",
}


def overlay_review(original, pending, reviews):
    """Return an independent copy; reject every mismatch before updating."""
    if not isinstance(original,dict) or original.get("total_selected_band_related")!=37:
        raise ValueError("Unexpected original 37-video baseline")
    originals=original["cases"]
    old={r["video_id"]:r for r in originals}
    if len(old)!=37:raise ValueError("Duplicate old video ID")

    selected=pending.get("items",[])
    selection={x["video_id"]:x for x in selected}
    if len(selected)!=8 or len(selection)!=8 or not pending.get("auto_posting") is False:
        raise ValueError("Frozen eight-video selection must be unique and non-posting")
    human={row["video_id"]:row for row in reviews}
    if len(reviews)!=8 or len(human)!=8:
        raise ValueError("Human verdict file must contain exactly 8 distinct video IDs")
    if set(human)!=set(selection) or set(human)!=set(EXPECTED_HUMAN):
        raise ValueError("Unexpected human verdict or selected video ID")
    for vid,entry in selection.items():
        if entry.get("review_label") is not None:
            raise ValueError("Original pending review cannot be prelabelled")
        if vid not in old:
            raise ValueError("Selected case not in frozen 37-case dataset")
        if old[vid]["channel_id"]!=entry["channel_id"]:
            raise ValueError("Changed uploader ID")
        source=entry["metadata_status_before_human_review"]
        expected="metadata_supported_reaction" if source=="supported_reaction" else (
            "metadata_assessed_nonreaction" if source=="exclude_by_reactions_only_policy" else None)
        if expected is None or old[vid]["truth_provenance"]!=expected:
            raise ValueError("Original metadata provenance was rewritten")
        row=human[vid]
        if (row.get("reviewer")!="human" or
            row.get("review_label")!=EXPECTED_HUMAN[vid]):
            raise ValueError("Missing/invalid explicit human verdict")

    # Copy all original public fields while keeping baseline available as
    # a separate file. Human followups must not change classifier output.
    report=json.loads(json.dumps(original))
    changed=[]
    for r in report["cases"]:
        vid=r["video_id"]
        if vid not in human:continue
        before=r["truth_provenance"]
        answer=human[vid]["review_label"]
        after="verified_reaction" if answer=="reaction" else "verified_nonreaction"
        if SOURCE_ALLOWED[answer]!=before:
            raise ValueError("Human followup conflicts with original metadata status; require separate dispute audit")
        r["truth_provenance"]=after
        r["human_followup_reviewed"]=True
        r["human_label_source"]="data/reaction_quality_next8_human_labels_20261022.csv"
        r["previous_metadata_provenance"]=before
        changed.append({"video_id":vid,"previous_metadata_provenance":before,
                        "human_verdict":answer,"new_provenance":after,
                        "classifier_tier_unchanged":r["classifier_tier"]})
    if len(changed)!=8:raise ValueError("Expected exactly 8 upgraded labels")

    count=Counter(x["truth_provenance"] for x in report["cases"])
    reactions=Counter(x["classifier_tier"] for x in report["cases"]
                      if x["truth_provenance"]=="verified_reaction")
    nonreactions=Counter(x["classifier_tier"] for x in report["cases"]
                         if x["truth_provenance"]=="verified_nonreaction")
    expected={
      "verified_reaction":14,
      "verified_nonreaction":9,
      "metadata_supported_reaction":12,
      "metadata_assessed_nonreaction":2,
    }
    if dict(count)!=expected:raise ValueError("Unexpected final provenance totals")
    if reactions["none"] or nonreactions["strong"]:
        raise ValueError("Confirmed valid reaction missed or confirmed nonreaction incorrectly strong")

    report["source"]="Same 37 selected cases; eight user-submitted human followups added with independent provenance"
    report["confirmed_reactions"]=count["verified_reaction"]
    report["confirmed_nonreactions"]=count["verified_nonreaction"]
    report["metadata_supported_reactions_not_video_verified"]=count["metadata_supported_reaction"]
    report["metadata_assessed_nonreactions_not_video_verified"]=count["metadata_assessed_nonreaction"]
    report["human_reaction_tiers"]={t:reactions[t] for t in ("strong","review","none")}
    report["human_nonreaction_tiers"]={t:nonreactions[t] for t in ("strong","review","none")}
    report["eight_human_followups_received"]=True
    report["eight_human_followup_overlays"]=sorted(changed,key=lambda x:x["video_id"])
    report["baseline_remains_unmodified"]=True
    report["reviewer"]="human (explicit user-supplied decisions); assistant did not independently watch videos"
    report["no_population_metrics"]=True
    report["no_discord_authorization"]=True
    report["selection_warning"]="Two risk-enriched handpicked cohorts (30 and 7); eight further cases chosen from the same thirty. Not a new independent population or representative sample. Detector uses frozen titles without new descriptions/tags/video viewing. Strong metadata signal is NOT a Discord send permission."
    return report


def evaluate_followup():
    return overlay_review(
        baseline_evaluate(),
        json.loads(PENDING.read_text(encoding="utf-8")),
        _csv(HUMAN),
    )


def main():
    report=evaluate_followup()
    OUTPUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print("EIGHT HUMAN VERDICTS APPLIED TO THE EXISTING 37 DISTINCT VIDEOS — OFFLINE")
    for key in ("total_selected_band_related","confirmed_reactions","confirmed_nonreactions",
                "metadata_supported_reactions_not_video_verified",
                "metadata_assessed_nonreactions_not_video_verified",
                "human_reaction_tiers","human_nonreaction_tiers"):
        print(f" {key}: {report[key]}")
    print("All eight reviewer submissions were retained; no new API calls, Discord sends or bookmark writes.")


if __name__=="__main__":
    main()
