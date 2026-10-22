"""Attach independent human labels to evaluation candidates before scoring.

CSV columns: video_id,ground_truth,reviewed_by,review_notes.
Never infer labels from titles, model predictions or notification history.
"""
import argparse
import csv
import json
from pathlib import Path

ALLOWED_LABELS = {"match", "not_match"}
ALLOWED_REVIEWERS = {"user", "human", "manual"}


def add_labels(dataset, csv_path):
    known = {}
    with Path(csv_path).open("r", newline="", encoding="utf-8") as inp:
        reader = csv.DictReader(inp)
        required = {"video_id", "ground_truth", "reviewed_by"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Review CSV needs video_id, ground_truth, reviewed_by columns")
        for row in reader:
            vid = (row.get("video_id") or "").strip()
            label = (row.get("ground_truth") or "").strip()
            reviewer = (row.get("reviewed_by") or "").strip()
            if not label:
                continue
            if label not in ALLOWED_LABELS or reviewer not in ALLOWED_REVIEWERS:
                raise ValueError(f"Invalid/unverified label for {vid}")
            if vid in known:
                raise ValueError(f"Duplicate labelled video ID {vid}")
            known[vid] = (label, reviewer)
    result = json.loads(json.dumps(dataset))
    count = 0
    for sample in result.get("samples", []):
        entry = known.pop(sample.get("video_id", ""), None)
        if entry:
            sample["ground_truth"], sample["reviewed_by"] = entry
            count += 1
    if known:
        print(f"Note: {len(known)} labelled IDs are absent from this sampled dataset.")
    print(f"Attached {count} human-reviewed labels; remaining videos stay unlabelled.")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="vision-unlabelled-candidates.json")
    ap.add_argument("--labels", default="data/vision-labels.csv")
    ap.add_argument("--output", default="vision-labelled-candidates.json")
    args = ap.parse_args()
    dataset = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = add_labels(dataset, args.labels)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                 encoding="utf-8")


if __name__ == "__main__":
    main()
