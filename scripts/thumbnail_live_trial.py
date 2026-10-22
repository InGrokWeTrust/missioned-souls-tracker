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
    {"id":"hf03Voz2QBM","channel":"Taylor John Hardin Reactions","label":"Hidden Missioned Souls example","expected":True},
    {"id":"kH6vzMgvfS0","channel":"WILBURN MUSIC REACTIONS","label":"Explicit Missioned Souls title","expected":True},
    {"id":"Lr4gUjZVjT0","channel":"Taylor John Hardin Reactions","label":"Nene Royal music reaction","expected":False},
    {"id":"n9ZfJX8mDJc","channel":"Taylor John Hardin Reactions","label":"Guitar tunings discussion","expected":False},
    {"id":"tHS3hkb7uV0","channel":"Taylor John Hardin Reactions","label":"One Direction reaction","expected":False},
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
        row = {**example, "fetched":False, "match":None, "reason":"", "thumbnail_url":"", "ocr_excerpt":""}
        try:
            url, data, width, height = fetch_real_thumbnail(example["id"])
            text = extract_thumbnail_text(data)
            match, reason = text_match(text)
            row.update(fetched=True, match=match, reason=reason, thumbnail_url=url,
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
        "false_negative":sum(r["match"] is False and r["expected"] for r in rows)
    }
    Path("thumbnail-live-trial-results.json").write_text(json.dumps(out,indent=2),encoding="utf-8")
    print("Summary:", {k:out[k] for k in
          ("fetched","true_positive","true_negative","false_positive","false_negative")})
    # Test runner should report a failure when the known hidden reaction is missed.
    if not rows[0]["fetched"] or not rows[0]["match"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(trial())
