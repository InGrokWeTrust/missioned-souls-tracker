"""Conservative, zero-fee Missioned Souls keyword evidence classifier.

Read-only: no credentials, network, Discord, bookmarks, or machine learning.
Outputs evidence and a *suggestion*, not a verified reaction identity.

Only a clearly identifiable artist in the title, or distinct 'Nitro Neisha'
on a tracked reactor, receives a strong signal. Member names/instruments are
potential matches for human review, NOT eligible for automatic notification.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

BAND = re.compile(r"(?<![a-z])missioned[\W_]*souls(?![a-z])|(?<![a-z])missionedsouls(?![a-z])", re.I)
BAND_VARIANT = re.compile(r"(?<![a-z])mission[\W_]*souls(?![a-z])", re.I)
NITRO = re.compile(r"\bnitro[\s\-_]*n(?:ei|ee)i?sha\b", re.I)
REACTION = re.compile(r"\breact(?:s|ed|ion|ions|ing|or)?\b|\breview(?:s|ed|ing)?\b|\bfirst[\s-]*time\b|\bmy[\s-]*thoughts\b", re.I)
PERFORMANCE = re.compile(r"\bperformance\b|\bcover\b|\bfamily[\s-]*band\b", re.I)
ROLE = {
    "neisha": re.compile(r"\blead[\s-]*guitar\b|\bguitar(?:ist)?\b|\bsolo\b|\bshred(?:s|ding|der)?\b|\briff\b", re.I),
    "stacey": re.compile(r"\bvocal(?:s|ist)?\b|\bsing(?:er|ing|s)?\b|\brhythm[\s-]*guitar\b", re.I),
    "naces": re.compile(r"\bbass(?:ist)?\b|\bbase[\s-]*guitar\b|\bkeyboard(?:s|ist)?\b|\bkeys\b", re.I),
    "ice": re.compile(r"\bdrum(?:s|mer|ming)?\b|\bpercussion\b|\bbare[\s-]*foot\b", re.I),
    "sheena": re.compile(r"\bvocal(?:s|ist)?\b|\bsing(?:er|ing|s)?\b", re.I),
    "secan": re.compile(r"\bbass(?:ist)?\b|\bmix(?:er|ing)?\b|\bsound[\s-]*engineer\b", re.I),
}
PEOPLE = {
    "neisha": re.compile(r"\bn(?:ei|ee)i?sha\b", re.I),
    "stacey": re.compile(r"\bstace?y\b", re.I),
    "naces": re.compile(r"\bnace(?:s|['’]s)?\b", re.I),
    # Ice alone is not evidence of a band member.
    "ice": re.compile(r"\b(?:isiah|isaiah|ice)\b", re.I),
    "sheena": re.compile(r"\b(?:sheena|shena)\b", re.I),
    "secan": re.compile(r"\bsecan\b", re.I),
}
GENERIC_EXCLUSIONS = re.compile(
    r"\b(?:ice[\s-]*cream|ice[\s-]*hockey|on[\s-]*ice|stacey[\s-]*solomon)\b", re.I
)
MAX_DESCRIPTION_PREFIX = 650


def clean(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\u200b-\u200d\ufeff]", "", text)


def _member_roles(text: str) -> dict[str, str]:
    matches = {}
    for name, pattern in PEOPLE.items():
        if not pattern.search(text):
            continue
        # Require role nearby, to avoid matching unrelated names in boilerplate
        for m in pattern.finditer(text):
            local = text[max(0, m.start() - 45):min(len(text), m.end() + 58)]
            if ROLE[name].search(local):
                if name == "ice" and GENERIC_EXCLUSIONS.search(local):
                    continue
                matches[name] = local.strip()
                break
    return matches


def classify(title: str, description: str = "", tags=None, *, tracked_reactor=True) -> dict:
    """Return tier, reasons and supporting matches without claiming truth.

    auto_candidate: only for strong title identity. Integration is report-only
    and never sends a notification from this classification.
    """
    title = clean(title)
    description = clean(description)[:MAX_DESCRIPTION_PREFIX]
    tags = tags if isinstance(tags, list) else []
    norm_tags = [clean(t).strip().lower().lstrip("#") for t in tags[:30]
                 if isinstance(t, str)]
    evidence = []
    reasons = []
    tier = "none"
    auto_candidate = False
    if not tracked_reactor:
        return {"tier":"none", "auto_candidate": False,
                "evidence":[], "reasons":["channel is not a tracked reactor"]}

    title_band = bool(BAND.search(title))
    title_nitro = bool(NITRO.search(title))
    title_variant = bool(BAND_VARIANT.search(title))
    description_band = bool(BAND.search(description))
    tag_band = any(t.replace(" ", "").replace("-", "").replace("_", "") == "missionedsouls"
                   for t in norm_tags)
    title_context = bool(REACTION.search(title) or PERFORMANCE.search(title))
    description_context = bool(REACTION.search(description))
    title_members = _member_roles(title)
    desc_members = _member_roles(description)

    if title_band:
        tier = "strong"
        auto_candidate = True
        evidence.append("exact band name in title")
    elif title_nitro:
        tier = "strong"
        auto_candidate = True
        evidence.append("distinctive 'Nitro Neisha' phrase in title")
    else:
        if title_variant:
            evidence.append("unverified artist spelling variant in title")
        if description_band:
            evidence.append("exact band name near start of description")
        if tag_band:
            evidence.append("exact band-name tag")
        if title_members:
            evidence.append("member + instrument/role in title: " + ", ".join(sorted(title_members)))
        if desc_members:
            evidence.append("member + instrument/role in description: " + ", ".join(sorted(desc_members)))
        if title_context or description_context:
            reasons.append("reaction/performance context detected")
        # Only elevate to review; descriptions are often copied boilerplate
        # and names/roles are not sufficiently unique for automatic sending.
        if evidence:
            tier = "review"
        if len(title_members) >= 2 and (REACTION.search(title) or PERFORMANCE.search(title)):
            reasons.append("multiple distinct band members and video context")
        elif len(title_members) == 1:
            reasons.append("single name/role can refer to another performer")
        if title_variant:
            reasons.append("spelling variant is not unambiguous")
        if description_band or tag_band:
            reasons.append("description/tag reference could be boilerplate")
    if not evidence:
        reasons.append("no sufficiently distinctive band evidence")
    return {"tier":tier, "auto_candidate":auto_candidate,
            "evidence":evidence, "reasons":reasons}


def report_unmatched(videos, youtube, *, max_videos=150):
    """Use batched videos.list(snippet) to inspect previously title-skipped videos.

    Inputs have already come from tracked uploads playlists; only returns a
    read-only summary. No changes to notification queue or Discord.
    """
    shortlist = []
    seen = set()
    for vid in sorted(videos, key=lambda x: x.get("published_at", ""), reverse=True):
        video_id = vid.get("video_id")
        if (not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
                or video_id in seen):
            continue
        seen.add(video_id)
        shortlist.append(vid)
        if len(shortlist) >= max_videos:
            break
    meta = {}
    for start in range(0, len(shortlist), 50):
        ids = [v["video_id"] for v in shortlist[start:start + 50]]
        result = youtube.videos().list(part="snippet", id=",".join(ids)).execute()
        for item in result.get("items", []):
            if item.get("id") in ids:
                meta[item["id"]] = item.get("snippet", {})
    summary = {"examined":len(shortlist), "strong":[], "review":[], "none":0,
               "metadata_missing":0}
    for v in shortlist:
        snippet = meta.get(v["video_id"])
        if snippet is None:
            summary["metadata_missing"] += 1
            continue
        outcome = classify(v.get("title", ""),
                           snippet.get("description", ""),
                           snippet.get("tags", []))
        if outcome["tier"] in ("strong", "review"):
            summary[outcome["tier"]].append({
                "video_id":v["video_id"],
                "title":v.get("title", ""),
                "channel":v.get("channel", ""),
                "evidence":outcome["evidence"], "reasons":outcome["reasons"]
            })
        else:
            summary["none"] += 1
    return summary
