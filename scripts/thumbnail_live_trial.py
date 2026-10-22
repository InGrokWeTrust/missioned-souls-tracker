"""Read-only OCR trial on real, public YouTube thumbnail files.

This script has no YouTube API key, no Discord integration and no bookmark writes.
The video IDs were selected from channels tracked by Missioned Souls Tracker.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image
from io import BytesIO

from thumbnail_detector import extract_thumbnail_text, text_match

SAMPLES = [
    {"id":"hf03Voz2QBM","channel":"Taylor John Hardin Reactions","label":"Hidden Missioned Souls example","title":"Nitro Neisha TEARS It Up On This Classic!","expected":True},
    {"id":"kH6vzMgvfS0","channel":"WILBURN MUSIC REACTIONS","label":"Explicit Missioned Souls title","title":"THIS 11 YEAR OLD DRUMMER IS UNREAL - MISSIONED SOULS","expected":True},
    {"id":"Lr4gUjZVjT0","channel":"Taylor John Hardin Reactions","label":"Nene Royal music reaction","title":"Nene Doesn\u0027t Miss A SINGLE Note.","expected":False},
    {"id":"n9ZfJX8mDJc","channel":"Taylor John Hardin Reactions","label":"Guitar tunings discussion","title":"Let\u0027s Discuss Dany\u0027s Guitar Tunings","expected":False},
    {"id":"tHS3hkb7uV0","channel":"Taylor John Hardin Reactions","label":"One Direction reaction","title":"Pro Musician\u0027s First Time Hearing One Direction","expected":False},
]


def fetch_real_thumbnail(video_id):
    """Use the YouTube thumbnail CDN; choose an actual image, not a 404 placeholder."""
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        url = f"https://i.ytimg.com/vi/{video_id}/{name}.jpg"
        response = requests.get(url, timeout=12, headers={"User-Agent":"Mozilla/5.0"})
        if response.status_code != 200:
            continue
        response.raise_for_status()
        if len(response.content) > 2_000_000 or not response.headers.get("Content-Type","").startswith("image/"):
            continue
        with Image.open(BytesIO(response.content)) as image:
            width, height = image.size
            if width < 320 or height < 180:
                continue
        return url, response.content, width, height
    raise RuntimeError(f"No valid public YouTube thumbnail available for {video_id}")


def trial():
    rows = []
    for example in SAMPLES:
        row = {**example, "fetched":False, "match":None, "reason":"", "thumbnail_url":"", "ocr_excerpt":"", "combined_match":None, "detected_by":""}
        try:
            url, data, width, height = fetch_real_thumbnail(example["id"])
            text = extract_thumbnail_text(data)
            match, reason = text_match(text)
            combined = match or "missioned souls" in example["title"].lower()
            found_by = "title" if "missioned souls" in example["title"].lower() else ("thumbnail OCR" if match else "none")
            row.update(fetched=True, match=match, combined_match=combined, detected_by=found_by,
                       reason=reason, thumbnail_url=url,
                       width=width, height=height,
                       ocr_excerpt=" ".join(text.split())[:400])
        except Exception as exc:
            row["reason"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
        status=("MATCH" if row["match"] else "NO MATCH") if row["fetched"] else "FETCH/OCR FAILED"
        print(f"{status} | expected={example['expected']} | {example['channel']} | "
              f"{example['id']} | {row['reason']}")
        print(f"  OCR: {row['ocr_excerpt'] or '[unavailable]'}")
    out = {
        "trial_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":"Live public thumbnails on i.ytimg.com",
        "mode":"Read-only; no Discord and no API key",
        "samples":rows,
        "fetched":sum(bool(r["fetched"]) for r in rows),
        "true_positive":sum(r["match"] is True and r["expected"] for r in rows),
        "true_negative":sum(r["match"] is False and not r["expected"] for r in rows),
        "false_positive":sum(r["match"] is True and not r["expected"] for r in rows),
        "false_negative":sum(r["match"] is False and r["expected"] for r in rows),
        "combined_correct":sum(r["combined_match"] == r["expected"] for r in rows if r["fetched"]),
        "combined_false_positive":sum(r["combined_match"] is True and not r["expected"] for r in rows),
        "combined_false_negative":sum(r["combined_match"] is False and r["expected"] for r in rows)
    }
    Path("thumbnail-live-trial-results.json").write_text(json.dumps(out,indent=2),encoding="utf-8")
    print("Summary:", {k:out[k] for k in
          ("fetched","true_positive","true_negative","false_positive","false_negative",
           "combined_correct","combined_false_positive","combined_false_negative")})
    # Test runner should report a failure when the known hidden reaction is missed.
    if not rows[0]["fetched"] or not rows[0]["match"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(trial())
