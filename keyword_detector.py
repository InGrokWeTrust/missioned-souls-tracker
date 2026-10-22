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
    "naces": re.compile(r"\bbass(?:ist)?\b|\bbase(?:[\s-]*guitar)?\b|\bkeyboard(?:s|ist)?\b|\bkeys\b", re.I),
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
PAPER_DOVE = re.compile(r"\bpaper[\s_-]*dove\b", re.I)
TRIBUTE = re.compile(r"\btribute\b|\ba[\s-]+song[\s-]+for[\s-]+the[\s-]+unsung\b", re.I)
EVENT_NEWS = re.compile(
    r"\bfree\s+(?:worldwide\s+)?(?:live\s+)?concert\b|"
    r"\b(?:are\s+)?hosting\s+a\s+(?:free\s+)?live\s+concert\b|"
    r"\bfree\s+live\s+stream\s+this\s+weekend\b|"
    r"\breviewing\s+the\s+website\b|"
    r"\b(?:one|1)\s+million\s+views\b|\b1,?000,?000\s+views\b",
    re.I,
)
ARCHIVE_CHANNEL = re.compile(r"\breaction\s+archive\b", re.I)
OFFICIAL_BAND_CHANNEL_ID = "UCeTfwcnUVurw7Qrk_KF5Idw"
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
                if GENERIC_EXCLUSIONS.search(local):
                    continue
                matches[name] = local.strip()
                break
    return matches


def classify(title: str, description: str = "", tags=None, *, tracked_reactor=True,
             channel_name: str = "", channel_id: str = "",
             verified_reactor_source: bool = False) -> dict:
    """Return tier, reasons and supporting matches without claiming truth.

    auto_candidate: only for strong title identity. Integration is report-only
    and never sends a notification from this classification.
    """
    title = clean(title)
    channel_name = clean(channel_name)
    channel_id = clean(channel_id)
    description = clean(description)[:MAX_DESCRIPTION_PREFIX]
    tags = tags if isinstance(tags, list) else []
    norm_tags = [clean(t).strip().lower().lstrip("#") for t in tags[:30]
                 if isinstance(t, str)]
    evidence = []
    reasons = []
    tier = "none"
    auto_candidate = False
    review_priority = "none"
    if channel_id == OFFICIAL_BAND_CHANNEL_ID:
        return {"tier":"none", "auto_candidate":False, "review_priority":"none",
                "evidence":[], "reasons":["original band channel, not independent reaction"]}
    if not tracked_reactor:
        return {"tier":"none", "auto_candidate": False, "review_priority":"none",
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

    # A band name is artist ID, not proof of a reaction.
    # Explicit non-reaction topics take precedence over generic #reaction tags.
    if PAPER_DOVE.search(title):
        tier = "none"
        reasons.append("Paper Dove is a separate group, not a Missioned Souls performance")
    elif (title_band or title_nitro) and TRIBUTE.search(title):
        tier = "none"
        reasons.append("tribute or musical dedication is not a reaction to the band performing")
    elif EVENT_NEWS.search(title):
        tier = "none"
        reasons.append("concert promotion, website review, or popularity milestone is not a performance reaction")
    elif ARCHIVE_CHANNEL.search(channel_name) or ARCHIVE_CHANNEL.search(title):
        tier = "none"
        reasons.append("reaction archive is not a newly created third-party reaction")
    elif title_nitro:
        tier = "strong"
        evidence.append("distinctive 'Nitro Neisha' title on tracked reactor")
        auto_candidate = True  # Shadow evidence only. Never posts by itself.
    elif title_band and REACTION.search(title):
        tier = "strong"
        evidence.append("exact band name and reaction-specific language in title")
        auto_candidate = True  # Shadow evidence only.
    elif title_band:
        tier = "review"
        evidence.append("exact band name in title WITHOUT explicit reaction evidence")
        reasons.append("artist identity alone does not establish a reaction")
    else:
        if title_variant:
            evidence.append("unverified artist spelling variant in title")
        if description_band:
            evidence.append("exact band name near start of description")
        if tag_band and (title_context or description_band or title_members or desc_members):
            evidence.append("exact band-name tag supported by other clues")
        elif tag_band:
            reasons.append("isolated band tag ignored: possible copy/paste metadata")
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
    if tier == "review":
        review_priority = "normal"
        # The uploader's identity is a meaningful *prior*, not proof of
        # video content. This flag is only trusted when the caller has
        # authenticated the uploads playlist against the tracked registry.
        if title_band and verified_reactor_source and channel_id and (
                channel_id != OFFICIAL_BAND_CHANNEL_ID):
            review_priority = "high"
            evidence.append("band-title upload from verified tracked independent reactor")
            reasons.append("higher review priority due to source, but source alone does not prove reaction")
    if not evidence:
        reasons.append("no sufficiently distinctive band evidence")
    return {"tier":tier, "auto_candidate":auto_candidate,
            "review_priority":review_priority,
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
        result = youtube.videos().list(part="snippet,contentDetails,liveStreamingDetails", id=",".join(ids)).execute()
        for item in result.get("items", []):
            if item.get("id") in ids:
                meta[item["id"]] = item
    summary = {"examined":len(shortlist), "strong":[], "review":[], "none":0,
               "metadata_missing":0, "excluded_shorts_or_live":0}
    for v in shortlist:
        item = meta.get(v["video_id"])
        if item is None:
            summary["metadata_missing"] += 1
            continue
        snippet = item.get("snippet", {})
        title = v.get("title", "")
        broadcast = snippet.get("liveBroadcastContent", v.get("live_broadcast_content", "none"))
        duration = item.get("contentDetails", {}).get("duration", "PT0S")
        match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
        seconds = (int(match.group(1) or 0) * 3600 + int(match.group(2) or 0) * 60 +
                   int(match.group(3) or 0)) if match else 0
        if (broadcast in ("live", "upcoming") or item.get("liveStreamingDetails")
                or re.search(r"#shorts?\b|\bshorts\b", title, re.I)
                or 0 < seconds < 120):
            summary["excluded_shorts_or_live"] += 1
            continue
        outcome = classify(title, snippet.get("description", ""), snippet.get("tags", []),
                           channel_name=v.get("channel", ""),
                           channel_id=v.get("channel_id",""),
                           verified_reactor_source=bool(v.get("channel_id")))
        if outcome["tier"] in ("strong", "review"):
            summary[outcome["tier"]].append({
                "video_id":v["video_id"],
                "title":v.get("title", ""),
                "channel":v.get("channel", ""),
                "evidence":outcome["evidence"], "reasons":outcome["reasons"],
                "review_priority":outcome["review_priority"]
            })
        else:
            summary["none"] += 1
    return summary
