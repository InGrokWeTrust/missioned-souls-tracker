"""Audit 30 human-reviewed STRONG keyword videos without guessing away disagreements.

Source labels are never changed. A video labelled 'match' with an explicit
"not a reaction" note is marked DISPUTED and excluded from confirmed matches
until the human reviewer resolves it. The strong30 sample was risk-enriched;
its observed fractions cannot estimate population precision.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "data" / "keyword_strong30_human_labels_20261022.csv"
SELECTION = ROOT / "data" / "keyword_strong30_pending_review_20261022.json"
OUTPUT = ROOT / "keyword-strong30-human-results.json"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CONTRADICTION = re.compile(r"\b(?:not an actual reaction|not a reaction|no reaction)\b", re.I)


def load_labels(path):
    with Path(path).open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["video_id", "review_label", "reviewer", "notes"]:
            raise ValueError("Human CSV must have exactly the expected columns")
        rows = []
        seen = set()
        for row in reader:
            vid = row["video_id"]
            if not VIDEO_ID.fullmatch(vid) or vid in seen:
                raise ValueError(f"Invalid or repeated video ID: {vid}")
            if row["reviewer"] != "human" or row["review_label"] not in ("match", "not_match", "uncertain"):
                raise ValueError(f"Invalid/unverified human label: {vid}")
            rows.append(row)
            seen.add(vid)
    return rows


def evaluate(selection, labels):
    ids = selection.get("video_ids_in_review_order")
    if not isinstance(ids, list) or selection.get("sample_size") != 30 or len(ids) != 30:
        raise ValueError("Unrecognised frozen selection")
    if len(set(ids)) != 30 or len(labels) != 30 or [x["video_id"] for x in labels] != ids:
        raise ValueError("Reviewer labels do not exactly match frozen 30-video selection order")
    cases = []
    for row in labels:
        conflicting = bool(row["review_label"] == "match"
                           and CONTRADICTION.search(row.get("notes", "")))
        status = "disputed" if conflicting else row["review_label"]
        cases.append({
            "video_id": row["video_id"],
            "raw_user_label": row["review_label"],
            "raw_user_note": row.get("notes", ""),
            "effective_status": status,
            "needs_confirmation": conflicting,
        })
    matches = [c for c in cases if c["effective_status"] == "match"]
    nonmatches = [c for c in cases if c["effective_status"] == "not_match"]
    disputed = [c for c in cases if c["effective_status"] == "disputed"]
    unsure = [c for c in cases if c["effective_status"] == "uncertain"]
    consistent = len(matches) + len(nonmatches)
    return {
        "source_scan":selection.get("source"),
        "reviewed":len(cases),
        "raw_marked_match":sum(c["raw_user_label"] == "match" for c in cases),
        "raw_marked_not_match":sum(c["raw_user_label"] == "not_match" for c in cases),
        "confirmed_match":len(matches),
        "confirmed_not_match":len(nonmatches),
        "disputed":len(disputed),
        "uncertain":len(unsure),
        "strong_tier_sample":True,
        "consistent_labels":consistent,
        "strong_tier_disagreements_in_consistent_sample":len(nonmatches),
        "confirmed_match_fraction_of_consistent_sample":round(len(matches) / consistent,4) if consistent else None,
        "population_precision":None,
        "population_recall":None,
        "reaction_only_precision":None,
        "review_scope_requires_clarification":True,
        "content_suspected_nonreaction_but_marked_valid": [
            {"video_id":"6Eu4f5oqXnk","title_hint":"FREE worldwide concert livestream announcement"},
            {"video_id":"qqhUL5a0rik","title_hint":"Hosting FREE LIVE CONCERT announcement"},
            {"video_id":"vpYHLQr04LQ","title_hint":"Free livestream this weekend; website review"},
            {"video_id":"IjaXJDLaIEM","title_hint":"One million views celebration"},
            {"video_id":"gXWQQNUpKcA","title_hint":"Family band studio cover"},
            {"video_id":"VhwqMoL-t8A","title_hint":"Reaction archive; may be archive rather than new reaction"},
        ],
        "content_scope_note":"The user marked these examples as matches although titles suggest "
                             "concerts, announcements, covers or archives; do not change "
                             "labels from title alone. Explicitly clarify whether the tracker "
                             "should send only actual third-party reactions or all band-related "
                             "posts before allowing automatic alerts.",
        "sample_selection":selection.get("selection"),
        "limitations":[
            "Risk-enriched, one per channel, with a pre-confirmed positive control. Not a random sample.",
            "Some video titles concern announcements, tributes or original covers. "
            "User concept of 'valid' may include band-related content beyond reaction videos.",
            "Disputed label is excluded rather than inferred from the note or rewritten.",
            "No strong-tier alerts will be enabled from this experiment.",
        ],
        "per_video":cases,
    }


def main():
    labels = load_labels(LABELS)
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    report = evaluate(selection, labels)
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("HUMAN STRONG-TIER REVIEW — RAW LABELS PRESERVED")
    for key in ("reviewed","raw_marked_match","raw_marked_not_match","confirmed_match",
                "confirmed_not_match","disputed","uncertain","consistent_labels",
                "strong_tier_disagreements_in_consistent_sample",
                "confirmed_match_fraction_of_consistent_sample"):
        print(f" {key}: {report[key]}")
    print("Disputed IDs:", ",".join(r["video_id"] for r in report["per_video"] if r["needs_confirmation"]))
    print("WARNING: stratified/risk-enriched sample; no real-world precision or recall claim.")
    print("WARNING: Human review may be evaluating band-related content, not strictly reactions.")


if __name__ == "__main__":
    main()
