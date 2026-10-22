"""Low-cost thumbnail text recognition for otherwise unmatched reactor uploads.

No vision model, facial identification, paid OCR service, or video download.
This module reads *thumbnail text only*. Ambiguous results are never auto-posted.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from io import BytesIO
import re
import subprocess
from urllib.parse import urlparse

import requests

BAND = "missionedsouls"
MAX_IMAGE_BYTES = 2_000_000
ALLOWED_IMAGE_HOSTS = {"i.ytimg.com", "img.youtube.com"}
SAFE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
SUPPORTING_WORDS = ("neisha", "missioned", "soulmates")


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def text_match(ocr_text):
    """Return (match, reason) without equating ordinary 'Neisha' mentions to a hit.

    OCR frequently misreads stylised 'Missioned Souls', including the supplied
    Taylor John Hardin / hf03Voz2QBM example ('NMissionevySours').
    """
    words = re.findall(r"[A-Za-z]{4,}", ocr_text or "")
    candidates = [normalize(w) for w in words]
    candidates += [normalize(words[i] + words[i + 1])
                   for i in range(len(words) - 1)]
    if any(BAND in word for word in candidates):
        return True, "exact thumbnail text"
    if any(SequenceMatcher(None, BAND, word).ratio() >= 0.90
           for word in candidates if 10 <= len(word) <= 20):
        return True, "high-confidence thumbnail text"
    supporting = any(term in normalize(ocr_text) for term in SUPPORTING_WORDS)
    if supporting and any(SequenceMatcher(None, BAND, word).ratio() >= 0.76
                          for word in candidates if 11 <= len(word) <= 20):
        return True, "approximate band text plus supporting clue"
    return False, "no reliable Missioned Souls thumbnail match"


def metadata_match(title, snippet):
    """Use strong description/tag signals but avoid generic channel boilerplate."""
    if "missioned souls" in title.lower():
        return True, "title"
    desc = snippet.get("description", "") or ""
    tags = snippet.get("tags", []) or []
    if not isinstance(tags, list):
        tags = []
    beginning = desc[:650].lower()
    tagged = any(normalize(t) == BAND for t in tags if isinstance(t, str))
    has_band = "missioned souls" in beginning
    relevant_title = bool(re.search(
        r"\breac(?:t|tion)\w*\b|\breview\b|\bneisha\b|\bfirst time\b|\bcover\b",
        title, re.I))
    relevant_description = bool(re.search(r"\breac(?:t|tion)\w*\b", beginning, re.I))
    if (has_band or tagged) and (relevant_title or relevant_description):
        return True, "description/tags with reaction context"
    return False, ""


def extract_thumbnail_text(image_bytes, *, executor=None):
    """Run local Tesseract; no external OCR API calls."""
    from PIL import Image, ImageOps
    if not 0 < len(image_bytes) <= MAX_IMAGE_BYTES:
        raise ValueError("thumbnail size exceeds limit or is empty")
    with Image.open(BytesIO(image_bytes)) as original:
        original.load()
        if original.width > 4096 or original.height > 4096:
            raise ValueError("thumbnail dimensions exceed limit")
        rgb = original.convert("RGB")
        # Two layouts handle both compact header labels and large captions.
        width, height = rgb.size
        rgb = rgb.resize((width * 2, height * 2))
        gray = ImageOps.autocontrast(ImageOps.grayscale(rgb))
        png = BytesIO()
        gray.save(png, format="PNG")
        payload = png.getvalue()

    runner = executor if executor is not None else subprocess.run
    found = []
    for psm in ("11", "6"):
        result = runner(
            ["tesseract", "stdin", "stdout", "--psm", psm],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=12, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Tesseract failed: " +
                               result.stderr.decode("utf-8", errors="replace")[:150])
        found.append(result.stdout.decode("utf-8", errors="replace"))
    return "\n".join(found)


def inspect_thumbnail(video, *, getter=None, extractor=None):
    """Return (matched, reason); network/OCR failure raises for retry next run."""
    video_id = video.get("video_id", "")
    if not SAFE_VIDEO_ID.fullmatch(video_id):
        raise ValueError("invalid YouTube video ID")
    default = "https://i.ytimg.com/vi/" + video_id + "/hqdefault.jpg"
    url = video.get("thumbnail") or default
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise ValueError("thumbnail host is not an approved YouTube CDN")
    get = getter if getter is not None else requests.get
    response = get(url, timeout=7)
    response.raise_for_status()
    kind = response.headers.get("Content-Type", "").lower()
    if kind and not kind.startswith("image/"):
        raise ValueError("thumbnail response is not an image")
    if len(response.content) > MAX_IMAGE_BYTES:
        raise ValueError("thumbnail is too large")
    read = extractor if extractor is not None else extract_thumbnail_text
    return text_match(read(response.content))
