"""Reproducible no-network keyword evaluation against clearly separated examples."""
from __future__ import annotations

import json
from pathlib import Path

from keyword_detector import classify

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data" / "keyword_trial_cases.json"
OUTPUT = ROOT / "keyword-trial-results.json"


def trial(cases):
    outcomes = []
    by_kind = {}
    for case in cases:
        actual = classify(case["title"], case.get("description", ""),
                          case.get("tags", []))
        correct = actual["tier"] == case["expect"]
        by_kind.setdefault(case["kind"], {"total": 0, "agreed": 0})
        by_kind[case["kind"]]["total"] += 1
        by_kind[case["kind"]]["agreed"] += int(correct)
        outcomes.append({
            "video_id":case.get("video_id"),
            "title":case["title"], "kind":case["kind"],
            "expected_tier":case["expect"], "actual_tier":actual["tier"],
            "agrees_with_fixture":correct,
            "evidence":actual["evidence"], "source":case.get("source", ""),
        })
    return {
        "total_examples":len(cases),
        "agree_with_expected_tier":sum(x["agrees_with_fixture"] for x in outcomes),
        "by_type":by_kind,
        "examples":outcomes,
        "limitations":"Curated positives and constructed edge cases are NOT a random, "
                      "human-labelled sample. These results cannot estimate "
                      "real-world precision or recall, or validate automatic posting.",
    }


def main():
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    result = trial(cases)
    OUTPUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    for k, item in result["by_type"].items():
        print(f"{k}: {item['agreed']}/{item['total']} agreed with expected tier")
    print(f"TOTAL {result['agree_with_expected_tier']}/{result['total_examples']}")
    print(result["limitations"])
    if result["agree_with_expected_tier"] != result["total_examples"]:
        raise SystemExit("Keyword trial mismatch; inspect keyword-trial-results.json")


if __name__ == "__main__":
    main()
