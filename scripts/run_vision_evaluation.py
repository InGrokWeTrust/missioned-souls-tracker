"""Optional, paid evaluation only. No production tracker / Discord writes.

Usage:
  python -m scripts.run_vision_evaluation --input vision-unlabelled-candidates.json \
    --max-calls 10 --budget-usd 0.50 --execute-paid-vision

Requires OPENAI_API_KEY. Every video remains unlabelled until independently
reviewed by a human; precision/recall is not calculated from model guesses.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from vision_evaluator import analyze, score_labelled, DEFAULT_MODEL

PRICES_PER_MILLION = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-sol": (2.00, 10.00),
}


def cost_from_usage(usage, model):
    if model not in PRICES_PER_MILLION:
        raise ValueError("No verified price configured; abort to avoid unexpected charges")
    if not isinstance(usage.get("input_tokens"), int) or not isinstance(usage.get("output_tokens"), int):
        raise ValueError("API token usage unavailable, cannot enforce cost budget")
    input_price, output_price = PRICES_PER_MILLION[model]
    return (usage["input_tokens"] * input_price +
            usage["output_tokens"] * output_price) / 1_000_000


def evaluate(dataset, *, api_key, model, max_calls, budget_usd, classifier=analyze):
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    if model not in PRICES_PER_MILLION:
        raise ValueError("Unsupported model or unknown pricing")
    if not 1 <= max_calls <= 200:
        raise ValueError("max_calls must be between 1 and 200")
    if not 0 < budget_usd <= 10:
        raise ValueError("budget_usd must be in (0, 10]")
    rows = list(dataset.get("samples", []))
    spent = 0.0
    results = []
    for sample in rows:
        if len(results) >= max_calls or spent >= budget_usd:
            break
        try:
            classified = classifier(sample, api_key=api_key, model=model)
            usage = classified.get("usage") or {}
            charge = cost_from_usage(usage, model)
            spent += charge
            result = {
                "video_id": sample["video_id"],
                "title": sample.get("title", ""),
                "channel": sample.get("channel", ""),
                "prediction": classified["label"],
                "reason": classified["vision"]["reason"],
                "visible_evidence": classified["vision"]["evidence"],
                "metadata": classified["metadata"],
                "estimated_usd": round(charge, 8),
                "ground_truth": sample.get("ground_truth"),
                "reviewed_by": sample.get("reviewed_by"),
            }
            results.append(result)
            print(f"Evaluated video {sample['video_id']} -> {result['prediction']}, "
                  f"running estimated cost USD {spent:.5f}")
        except Exception as exc:
            # Do not leak an HTTP body or headers potentially containing secrets.
            print(f"Evaluation failed for {sample.get('video_id', '<unknown>')}: "
                  f"{type(exc).__name__}")
            break
    metrics = score_labelled(results)
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "executed": len(results),
        "total_unlabelled_candidates": len(rows),
        "estimated_api_cost_usd": round(spent, 6),
        "budget_usd": budget_usd,
        "note": "Usage cost is estimated from published token prices; excludes taxes and other charges. "
                "One last request can cross the budget boundary.",
        "metrics": metrics,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="vision-unlabelled-candidates.json")
    parser.add_argument("--output", default="vision-model-evaluation.json")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(PRICES_PER_MILLION))
    parser.add_argument("--max-calls", type=int, default=10)
    parser.add_argument("--budget-usd", type=float, default=0.5)
    parser.add_argument("--execute-paid-vision", action="store_true")
    args = parser.parse_args()
    if not args.execute_paid_vision:
        raise SystemExit("Paid model analysis disabled: supply --execute-paid-vision explicitly.")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY unavailable. No billable requests made.")
    dataset = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(dataset, dict) or not isinstance(dataset.get("samples"), list):
        raise SystemExit("Invalid candidate sample data")
    report = evaluate(dataset, api_key=key, model=args.model,
                      max_calls=args.max_calls, budget_usd=args.budget_usd)
    Path(args.output).write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Saved", args.output)
    print("Metrics:", report["metrics"])
    if report["metrics"]["labelled"] < 30:
        print("Do not declare accuracy successful: not enough human-reviewed labels.")


if __name__ == "__main__":
    main()
