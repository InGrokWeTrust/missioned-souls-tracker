"""Score independently human-reviewed labels against saved keyword shadow tiers.

Always report the denominators and selection bias. The 46-item review sheet
was STRATIFIED by existing classifier tiers; its accuracy numbers are NOT a
real-world precision/recall estimate over all tracked reactors. No network,
Discord, production keys, or bookmark writes occur.
"""
from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "data" / "keyword_manual_labels_20261022.csv"
SNAPSHOT = ROOT / "data" / "keyword_audit_snapshot_20261022.json"
OUTPUT = ROOT / "keyword-human-label-results.json"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VALID_TIERS = {"strong", "review", "none"}


def read_human_labels(path):
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["video_id", "review_label", "reviewer", "notes"]:
            raise ValueError("Review labels CSV must retain the exact four columns")
        labels = {}
        for row in reader:
            vid = row["video_id"]
            if not VIDEO_ID.fullmatch(vid):
                raise ValueError("Invalid YouTube video ID in human labels")
            if row["review_label"] not in ("match", "not_match"):
                raise ValueError(f"Unverified classification for {vid}")
            if row["reviewer"] != "human":
                raise ValueError(f"Only explicitly human-reviewed labels permitted: {vid}")
            if vid in labels:
                raise ValueError(f"Duplicate human label: {vid}")
            labels[vid] = dict(row)
    if not labels:
        raise ValueError("No human reviews supplied")
    return labels


def score(snapshot, labels):
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Missing audit snapshot rows")
    by_id = {}
    for row in rows:
        vid = row.get("video_id", "")
        if not VIDEO_ID.fullmatch(vid) or vid in by_id:
            raise ValueError("Invalid or duplicate audit video ID")
        if row.get("tier") not in VALID_TIERS:
            raise ValueError("Invalid classifier tier in audited snapshot")
        by_id[vid] = row
    missing = set(labels).difference(by_id)
    unreviewed = set(by_id).difference(labels)
    if missing or unreviewed:
        raise ValueError(
            f"Labels and snapshot IDs disagree: {len(missing)} unknown labels, "
            f"{len(unreviewed)} unreviewed snapshot rows")
    predictions = []
    for vid, human in labels.items():
        row = by_id[vid]
        predictions.append({
            "video_id": vid,
            "review_label": human["review_label"],
            "keyword_tier": row["tier"],
            "sample_type": row.get("sample_type", ""),
            "detected_as_review_candidate": row["tier"] in ("strong", "review"),
        })
    counts = Counter(
        (r["keyword_tier"], r["review_label"]) for r in predictions)
    tp = sum(r["review_label"] == "match" and r["detected_as_review_candidate"]
             for r in predictions)
    tn = sum(r["review_label"] == "not_match" and not r["detected_as_review_candidate"]
             for r in predictions)
    fp = sum(r["review_label"] == "not_match" and r["detected_as_review_candidate"]
             for r in predictions)
    fn = sum(r["review_label"] == "match" and not r["detected_as_review_candidate"]
             for r in predictions)
    return {
        "reference_scan": snapshot.get("source"),
        "total_human_reviewed": len(predictions),
        "human_matches": sum(r["review_label"] == "match" for r in predictions),
        "human_nonmatches": sum(r["review_label"] == "not_match" for r in predictions),
        "matched_by_review_tier": counts[("review", "match")],
        "unrelated_by_review_tier": counts[("review", "not_match")],
        "missed_match_by_none_tier": counts[("none", "match")],
        "correctly_unmatched_by_none_tier": counts[("none", "not_match")],
        "strong_tier_human_reviewed": sum(r["keyword_tier"] == "strong" for r in predictions),
        "confusion_in_selected_sample": {
            "true_positive_review_or_strong": tp, "false_positive_review_or_strong": fp,
            "false_negative_none": fn, "true_negative_none": tn,
        },
        "positive_candidate_sample_fraction": round(tp / (tp + fp), 4)
        if tp + fp else None,
        "positive_detection_in_sample_fraction": round(tp / (tp + fn), 4)
        if tp + fn else None,
        "sampling": {
            "kind": "stratified: selected review cases, flagged nonmatches, and channel-balanced controls",
            "full_live_eligible": snapshot.get("unreviewed_total_eligible"),
            "unreviewed_strong_tier": snapshot.get("snapshot_strong_count"),
            "population_precision": None,
            "population_recall": None,
            "limitation": "The 46 videos were chosen using the classifier and audit clues. "
                          "No strong-tier video was evaluated in this set. Values above "
                          "describe these reviewed items ONLY, not tracker-wide performance.",
        },
        "per_video": predictions,
    }


def main():
    labels = read_human_labels(LABELS)
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    result = score(snapshot, labels)
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Human-reviewed audit: ", result["total_human_reviewed"])
    print("Reactions:", result["human_matches"],
          "| unrelated:", result["human_nonmatches"])
    print("Review tier: ", result["matched_by_review_tier"], "matches,",
          result["unrelated_by_review_tier"], "nonmatches")
    print("No-match tier:", result["missed_match_by_none_tier"], "misses,",
          result["correctly_unmatched_by_none_tier"], "correct exclusions")
    print("Strong tier evaluated:", result["strong_tier_human_reviewed"])
    print("No global precision/recall claimed: stratified evaluation sample.")
    print("Wrote:", OUTPUT.name)


if __name__ == "__main__":
    main()
