"""Apply five explicitly human-verified reaction/repost/discussion decisions.

This layer consumes the PREVIOUS frozen 30-video review and interim
metadata-only policy decisions. It does NOT alter either source. The five new
human verdicts are the sole overrides, preserving provenance per entry.
Read-only offline: no YouTube API, Discord or notification-state writes.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from scripts.score_keyword_reactions_only import (
    ADJUDICATION, evaluate as evaluate_prior, LABELS, SELECTION, load_labels,
)

ROOT = Path(__file__).resolve().parents[1]
HUMAN_FIVE = ROOT / "data" / "keyword_reactions_only_five_human_labels_20261022.csv"
OUTPUT = ROOT / "keyword-reactions-only-final-results.json"
EXPECTED_IDS = {
    "VhwqMoL-t8A", "cx1s0V_8Bu4", "JjFzKrfhNgg",
    "iVeWY7mTkrE", "gXWQQNUpKcA",
}
VALID_KINDS = {"reaction", "repost", "discussion"}


def read_five_labels(path=HUMAN_FIVE):
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["video_id", "content_type", "reviewer", "notes"]:
            raise ValueError("Five new human verdicts need the expected four columns")
        result = {}
        for row in reader:
            vid = row["video_id"]
            if vid in result:
                raise ValueError("Duplicate video ID in five-video human review")
            if row["reviewer"] != "human" or row["content_type"] not in VALID_KINDS:
                raise ValueError("Invalid human content verdict")
            result[vid] = dict(row)
    if set(result) != EXPECTED_IDS:
        raise ValueError("Five human verification IDs must match the previously unresolved set")
    return result


def final_evaluation(prior, human_verdicts):
    cases = prior.get("cases", [])
    if len(cases) != 30 or set(human_verdicts) != EXPECTED_IDS:
        raise ValueError("Only the five verified unresolved cases may be resolved")
    seen = set()
    updated = []
    for record in cases:
        record = dict(record)
        vid = record["video_id"]
        if vid in seen:
            raise ValueError("Duplicate source video ID")
        seen.add(vid)
        if vid in human_verdicts:
            if record["reactions_only_assessment"] != "recheck_video":
                raise ValueError("Refusing to overwrite an already adjudicated case")
            review = human_verdicts[vid]
            yes = review["content_type"] == "reaction"
            if not yes and record["new_keyword_tier"] == "strong":
                raise ValueError("Human-confirmed non-reaction cannot remain a strong keyword candidate")
            if yes and record["new_keyword_tier"] == "none":
                raise ValueError("Human-confirmed reaction must remain eligible for manual discovery")
            record["reactions_only_assessment"] = (
                "human_verified_reaction" if yes else "human_verified_nonreaction"
            )
            record["human_video_content_type"] = review["content_type"]
            record["human_verdict_notes"] = review["notes"]
            record["verified_by_new_video_watch"] = False
            record["human_followup_label_received"] = True
            record["assessment_basis"] = (
                "Follow-up human judgement: "
                + ("performance reaction" if yes else review["content_type"] + ", not a performance reaction")
            )
        else:
            if record["reactions_only_assessment"] == "recheck_video":
                raise ValueError("An unresolved case was not verified by the human review")
            record["human_video_content_type"] = None
            record["verified_by_new_video_watch"] = False
            record["human_followup_label_received"] = False
        record["would_auto_post"] = False
        updated.append(record)
    if len(seen) != 30:
        raise ValueError("Frozen reference sample was incomplete")
    counts = Counter(x["reactions_only_assessment"] for x in updated)
    expected = {
        "supported_reaction":19,
        "exclude_by_reactions_only_policy":6,
        "human_verified_reaction":3,
        "human_verified_nonreaction":2,
    }
    if dict(counts) != expected:
        raise ValueError("Unexpected counts; investigate review alignment or source changes")
    return {
        "policy":prior["policy"],
        "total":30,
        "earlier_metadata_supported_reactions":counts["supported_reaction"],
        "new_human_verified_reactions":counts["human_verified_reaction"],
        "earlier_metadata_supported_exclusions":counts["exclude_by_reactions_only_policy"],
        "new_human_verified_nonreactions":counts["human_verified_nonreaction"],
        "total_reaction_candidates":(counts["supported_reaction"] + counts["human_verified_reaction"]),
        "total_excluded_nonreactions":(
            counts["exclude_by_reactions_only_policy"] + counts["human_verified_nonreaction"]
        ),
        "unresolved_count":0,
        "human_content_labels_now_added":len(human_verdicts),
        "original_human_match_count":prior["original_user_match_count"],
        "original_human_not_match_count":prior["original_user_not_match_count"],
        "classifier_tiers_from_frozen_titles":prior["classifications_after_new_rules"],
        "global_population_precision":None,
        "global_population_recall":None,
        "reaction_specific_precision":None,
        "notes":[
            "Three new reactions and two new non-reactions are explicitly confirmed by the user.",
            "Earlier 19 reaction candidates are metadata-supported, not independently re-watched under the strict policy.",
            "Classifier tier and human truth are different: review-tier reactions remain review-only.",
            "Prior raw user labels and the earlier provisional adjudication are preserved unchanged.",
            "This purposefully risk-enriched 30-video sample cannot estimate production precision/recall.",
            "No automatic posting was enabled or carried out.",
        ],
        "cases":updated,
    }


def main():
    human=read_five_labels()
    prior=evaluate_prior(
        json.loads(ADJUDICATION.read_text(encoding="utf-8")),
        json.loads(SELECTION.read_text(encoding="utf-8")),
        load_labels(LABELS)
    )
    result=final_evaluation(prior,human)
    OUTPUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print("FINAL REACTIONS-ONLY HUMAN FOLLOW-UP (NO DISCORD)")
    for k in ("total", "earlier_metadata_supported_reactions",
              "new_human_verified_reactions", "earlier_metadata_supported_exclusions",
              "new_human_verified_nonreactions", "total_reaction_candidates",
              "total_excluded_nonreactions", "unresolved_count"):
        print(f" {k}: {result[k]}")
    print("Classifier strong/review/none:",result["classifier_tiers_from_frozen_titles"])
    print("Population precision/recall not measurable from this sample.")


if __name__=="__main__":
    main()
