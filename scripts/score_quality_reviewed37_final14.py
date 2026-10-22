"""Offline final 14 human-verdict overlay of the same 37 selected videos.

Original metadata snapshots, eight-review overlay and blank 14-case
review queue are immutable. The user's latest explicit verdict takes
precedence over an earlier submission: 13 reactions and one not_match.
A classifier-human disagreement is reported, NOT auto-corrected.
No YouTube, Discord, HTTP, production sender or state writes.
"""
from __future__ import annotations

from collections import Counter
import copy
import json

from scripts.score_quality_reviewed37 import ROOT, _csv
from scripts.score_quality_reviewed37_followup import evaluate_followup

PENDING = ROOT / "data" / "reaction_quality_remaining14_pending_review_20261022.json"
HUMAN = ROOT / "data" / "reaction_quality_remaining14_human_labels_20261022.csv"
OUTPUT = ROOT / "reaction-quality-reviewed37-with-final14.json"

EXPECTED_REACTIONS = frozenset({
    "ETb_O74yn20", "X2iIie9DGMQ", "emohzErkSxU",
    "4Irh5GOuklU", "zPZf_TsXeak", "qz3tZGs8nzo",
    "eg1mmc5N4_M", "qHOVcqgRRgA", "y2CRFwhqSqM",
    "wf86HLqLmN8", "8PMWuRU-H30", "XBCg8-44GJs",
    "IjaXJDLaIEM",
})
EXPECTED_NONREACTIONS = frozenset({"qqhUL5a0rik"})
EXPECTED_IDS = EXPECTED_REACTIONS | EXPECTED_NONREACTIONS
ORIGINAL_METADATA_EXCLUSIONS = frozenset({"IjaXJDLaIEM", "qqhUL5a0rik"})
METADATA_TO_PROVENANCE = {
    "supported_reaction": "metadata_supported_reaction",
    "exclude_by_reactions_only_policy": "metadata_assessed_nonreaction",
}


def apply_final_review(previous, pending, human_rows):
    """Validate exact submitted labels and provenance; return an independent report."""
    original = previous.get("cases", [])
    by_old = {row["video_id"]: row for row in original}
    if previous.get("total_selected_band_related") != 37 or len(original) != 37 or len(by_old) != 37:
        raise ValueError("Old 37-video baseline invalid")

    selected = pending.get("items", [])
    by_selected = {row["video_id"]: row for row in selected}
    by_human = {row["video_id"]: row for row in human_rows}
    if len(selected) != 14 or len(by_selected) != 14 or set(by_selected) != EXPECTED_IDS:
        raise ValueError("Expected exactly the frozen 14 review IDs")
    if len(human_rows) != 14 or len(by_human) != 14 or set(by_human) != EXPECTED_IDS:
        raise ValueError("Expected exactly 14 unique user-submitted IDs")
    if pending.get("youtube_api_calls") != 0 or pending.get("discord_posts") != 0:
        raise ValueError("Human review queue must not authorise network delivery")

    for vid, item in by_selected.items():
        if item.get("review_label") is not None or item.get("reviewer") is not None:
            raise ValueError("Frozen original blank review was changed")
        if vid not in by_old or item["channel_id"] != by_old[vid]["channel_id"]:
            raise ValueError("Original video or uploading channel mismatch")
        source = METADATA_TO_PROVENANCE.get(item.get("prior_metadata_status"))
        if source is None or source != by_old[vid]["truth_provenance"]:
            raise ValueError("Original metadata provenance changed")
        if (vid in ORIGINAL_METADATA_EXCLUSIONS) != (source == "metadata_assessed_nonreaction"):
            raise ValueError("Frozen exclusion identities changed")
        user_row = by_human[vid]
        expected = "reaction" if vid in EXPECTED_REACTIONS else "not_match"
        if (user_row.get("review_label") != expected
                or user_row.get("reviewer") != "human"
                or user_row.get("notes") != ""):
            raise ValueError("Human verdict differs from the user's latest explicit submission")

    report = copy.deepcopy(previous)
    changed, discrepancies = [], []
    for row in report["cases"]:
        vid = row["video_id"]
        if vid not in by_selected:
            continue
        previous_status = row["truth_provenance"]
        verdict = by_human[vid]["review_label"]
        row["truth_provenance"] = ("verified_reaction" if verdict == "reaction"
                                   else "verified_nonreaction")
        row["previous_metadata_provenance"] = previous_status
        row["human_label_source"] = "data/reaction_quality_remaining14_human_labels_20261022.csv"
        row["human_followup_reviewed"] = True
        row["original_classifier_tier_retained"] = row["classifier_tier"]
        conflict = verdict == "reaction" and previous_status == "metadata_assessed_nonreaction"
        row["human_metadata_disagreement"] = conflict
        changed.append({
            "video_id": vid, "human_verdict": verdict,
            "previous_metadata_provenance": previous_status,
            "classifier_tier_unchanged": row["classifier_tier"],
            "needs_policy_review": conflict,
        })
        if conflict:
            discrepancies.append({
                "video_id": vid,
                "human_verdict": verdict,
                "previous_metadata_provenance": previous_status,
                "existing_classifier_tier": row["classifier_tier"],
                "remedy": "Investigate title-only false negative; keep Discord unchanged",
            })

    if len(changed) != 14 or {row["video_id"] for row in discrepancies} != {"IjaXJDLaIEM"}:
        raise ValueError("Incomplete review update or unexpected disagreement")
    counts = Counter(row["truth_provenance"] for row in report["cases"])
    if counts != {"verified_reaction": 27, "verified_nonreaction": 10}:
        raise ValueError("37-video human-verdict totals not preserved")
    positive = Counter(row["classifier_tier"] for row in report["cases"]
                       if row["truth_provenance"] == "verified_reaction")
    negative = Counter(row["classifier_tier"] for row in report["cases"]
                       if row["truth_provenance"] == "verified_nonreaction")
    # Missed reactions are *reported*, not silently masked by forcing the
    # classifier to agree with labels. This is the purpose of an audit.
    missed = sorted(row["video_id"] for row in report["cases"]
                    if row["truth_provenance"] == "verified_reaction"
                    and row["classifier_tier"] == "none")
    strong_false_positives = sorted(row["video_id"] for row in report["cases"]
                                    if row["truth_provenance"] == "verified_nonreaction"
                                    and row["classifier_tier"] == "strong")
    report.update({
        "source": "Same selected 37 videos, now all human-reviewed; explicit revised final14 verdicts",
        "total_selected_band_related": 37,
        "confirmed_reactions": 27,
        "confirmed_nonreactions": 10,
        "metadata_supported_reactions_not_video_verified": 0,
        "metadata_assessed_nonreactions_not_video_verified": 0,
        "human_reaction_tiers": {key: positive[key] for key in ("strong","review","none")},
        "human_nonreaction_tiers": {key: negative[key] for key in ("strong","review","none")},
        "final_fourteen_verdicts_received": True,
        "final_fourteen_verdicts": sorted(changed, key=lambda row: row["video_id"]),
        "human_metadata_disagreements": discrepancies,
        "human_metadata_disagreement_count": len(discrepancies),
        "confirmed_reactions_excluded_by_classifier": missed,
        "strong_false_positive_video_ids": strong_false_positives,
        "original_metadata_exclusions_confirmed_nonreaction": ["qqhUL5a0rik"],
        "earlier_evidence_unchanged": True,
        "no_population_metrics": True,
        "no_discord_authorization": True,
        "reviewer": "User-submitted video-level verdicts; assistant has not watched videos",
        "selection_warning": "Risk-enriched 37-video cohort, not a representative sample. The final fourteen are previously provisional titles from the same thirty. One human-confirmed reaction conflicts with the old metadata-only exclusion (IjaXJDLaIEM). The concert announcement qqhUL5a0rik is human-confirmed not_match. No automatic classification or sending changes.",
    })
    return report


def evaluate_final():
    return apply_final_review(
        evaluate_followup(),
        json.loads(PENDING.read_text(encoding="utf-8")),
        _csv(HUMAN),
    )


def main():
    report = evaluate_final()
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("FINAL FOURTEEN HUMAN-REVIEW VERDICTS — READ-ONLY AUDIT")
    for field in ("total_selected_band_related","confirmed_reactions",
                  "confirmed_nonreactions","metadata_supported_reactions_not_video_verified",
                  "metadata_assessed_nonreactions_not_video_verified",
                  "human_reaction_tiers","human_nonreaction_tiers",
                  "human_metadata_disagreement_count",
                  "confirmed_reactions_excluded_by_classifier",
                  "strong_false_positive_video_ids"):
        print(f" {field}: {report[field]}")
    print("No YouTube API requests, Discord deliveries, or production bookmark writes.")


if __name__ == "__main__":
    main()
