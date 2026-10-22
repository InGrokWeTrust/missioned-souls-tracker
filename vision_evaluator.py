"""Evaluation-only Missioned Souls reaction detector using metadata and vision.

Never imported by the production tracker. Never sends Discord notifications,
changes notification bookmarks, or treats a model judgement as ground truth.
Calls to the paid Responses API require an explicit API key and evaluation run.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests

API_ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6-luna"
BAND_RE = re.compile(r"\bmissioned[\s_-]*souls\b", re.IGNORECASE)
REACTION_RE = re.compile(r"\breac(?:t|ts|ted|tion|tions|ting)\b|\breview\b|\bfirst time\b", re.I)
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
TRUSTED_HOSTS = {"i.ytimg.com", "img.youtube.com"}
MAX_IMAGE_SIZE = 2_000_000

PROMPT = """Classify a music-reaction video from its public thumbnail and provided metadata.
Goal: decide whether this specific video is a REACTION to the band Missioned Souls.
Rules:
- Read thumbnail text even if stylized, and explain exactly what text you can see.
- Neisha, a familiar face, a generic music title, a guitar solo, or a channel's past
  interest alone does NOT prove the band is Missioned Souls.
- A thumbnail can use unrelated visual or background content. Do not assume the
  artist or identities of people pictured without clear identifying evidence.
- Description may contain generic boilerplate listing many artists; only treat
  a reference as relevant if linked to this video.
- Be conservative: choose "uncertain" when evidence is weak or ambiguous.
- A video's title containing Missioned Souls is useful, but can be commentary,
  concert footage or fan material instead of a reaction: separate those cases.
- Do NOT invent an official performance link, artist identity, or words that
  cannot be read. Return a short evidence-based rationale.
Output in the requested JSON schema only."""

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["match", "not_match", "uncertain"]},
        "evidence": {"type": "string"},
        "reaction_evidence": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["label", "evidence", "reaction_evidence", "reason"],
    "additionalProperties": False,
}


def metadata_evidence(sample: dict[str, Any]) -> dict[str, Any]:
    """A strong signal and useful hints; never silently equate them with proof."""
    title = str(sample.get("title") or "")
    description = str(sample.get("description") or "")
    tags = sample.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    title_band = bool(BAND_RE.search(title))
    title_reaction = bool(REACTION_RE.search(title))
    exact_tags = [tag for tag in tags if isinstance(tag, str) and BAND_RE.fullmatch(tag.strip())]
    # Prefer a reference near video-specific context rather than in a long list.
    desc_prefix = description[:750]
    desc_band = bool(BAND_RE.search(desc_prefix))
    relevant_desc = bool(REACTION_RE.search(desc_prefix))
    return {
        "title_band": title_band,
        "title_reaction": title_reaction,
        "description_band": desc_band,
        "description_reaction": relevant_desc,
        "exact_artist_tags": bool(exact_tags),
        "strong_metadata": bool(
            (title_band and (title_reaction or relevant_desc))
            or (desc_band and (title_reaction or relevant_desc))
            or (exact_tags and (title_reaction or relevant_desc))
        ),
    }


def thumbnail_url(video_id: str) -> str:
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError("Invalid 11-character YouTube video ID")
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def download_thumbnail(url: str, *, getter=None) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_HOSTS:
        raise ValueError("Untrusted thumbnail host")
    get = getter or requests.get
    response = get(url, timeout=12, allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200 or len(response.content) > MAX_IMAGE_SIZE:
        raise ValueError("Invalid or oversized thumbnail response")
    content_type = response.headers.get("Content-Type", "").lower().split(";")[0]
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("Unsupported image content type")
    if not response.content:
        raise ValueError("Empty thumbnail")
    return response.content


def build_request(sample: dict[str, Any], image_bytes: bytes, *, model=DEFAULT_MODEL):
    evidence = metadata_evidence(sample)
    public_context = {
        "video_id": sample["video_id"],
        "channel": str(sample.get("channel") or "")[:150],
        "title": str(sample.get("title") or "")[:220],
        "description_excerpt": str(sample.get("description") or "")[:1000],
        "tags": [str(t)[:60] for t in (sample.get("tags") or [])[:12]],
        "metadata_signals": evidence,
    }
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": model,
        "store": False,
        "max_output_tokens": 600,
        "instructions": PROMPT,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": json.dumps(public_context, ensure_ascii=False)},
                {"type": "input_image", "image_url": "data:image/jpeg;base64," + encoded, "detail": "high"},
            ],
        }],
        "text": {"format": {
            "type": "json_schema", "name": "reaction_thumbnail_evidence",
            "strict": True, "schema": CLASSIFICATION_SCHEMA,
        }},
    }


def parse_response(payload: dict[str, Any]):
    if payload.get("status") != "completed":
        raise ValueError("Incomplete vision response; no classification accepted")
    texts = []
    for item in payload.get("output") or []:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
    if not texts:
        raise ValueError("Vision model returned no text")
    obj = json.loads("".join(texts))
    if (not isinstance(obj, dict)
            or set(obj) != {"label", "evidence", "reaction_evidence", "reason"}
            or obj["label"] not in {"match", "not_match", "uncertain"}
            or any(not isinstance(obj[k], str) for k in obj)):
        raise ValueError("Unexpected vision classification schema")
    return {k: v[:450] for k, v in obj.items()}


def analyze(sample: dict[str, Any], *, api_key: str, model=DEFAULT_MODEL,
            post=None, getter=None):
    """One billable vision request. Never called without explicit opt-in."""
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for billable vision analysis")
    video_id = sample.get("video_id", "")
    image = download_thumbnail(thumbnail_url(video_id), getter=getter)
    payload = build_request(sample, image, model=model)
    sender = post or requests.post
    response = sender(
        API_ENDPOINT,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    classification = parse_response(body)
    return {
        "video_id": video_id,
        "model": model,
        "metadata": metadata_evidence(sample),
        "vision": classification,
        "label": classification["label"],
        "usage": body.get("usage") or {},
    }


def score_labelled(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate only human-verified labels; 'uncertain' is an abstention."""
    approved = [r for r in rows if r.get("ground_truth") in {"match", "not_match"}
                and r.get("prediction") in {"match", "not_match", "uncertain"}]
    tp = sum(r["ground_truth"] == "match" and r["prediction"] == "match" for r in approved)
    tn = sum(r["ground_truth"] == "not_match" and r["prediction"] == "not_match" for r in approved)
    fp = sum(r["ground_truth"] == "not_match" and r["prediction"] == "match" for r in approved)
    fn = sum(r["ground_truth"] == "match" and r["prediction"] != "match" for r in approved)
    uncertain = sum(r["prediction"] == "uncertain" for r in approved)
    positives = sum(r["ground_truth"] == "match" for r in approved)
    coverage = sum(r["prediction"] != "uncertain" for r in approved)
    def divide(n, d):
        return round(n / d, 4) if d else None
    return {
        "labelled": len(approved), "true_positive": tp, "true_negative": tn,
        "false_positive": fp, "missed_positive_including_uncertain": fn,
        "uncertain": uncertain,
        "precision": divide(tp, tp + fp), "recall": divide(tp, positives),
        "coverage": divide(coverage, len(approved)),
        "false_positive_rate": divide(fp, fp + tn + sum(
            r["ground_truth"] == "not_match" and r["prediction"] == "uncertain"
            for r in approved)),
    }
