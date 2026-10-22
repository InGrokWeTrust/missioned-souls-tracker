"""Conservative eligibility for a newly discovered channel's pre-registration VODs.

This is intentionally narrower than the production title match. It is NOT a
general-purpose reaction-quality classifier and does not replace the separate
read-only classifier evaluation.
"""
import re

_REACTION = re.compile(
    r"\b(?:reactions?|reacts?|reacted|reacting)(?:\s+to)?\b"
    r"|\bfirst\s+time\s+(?:hearing|listening|watching)\b",
    re.IGNORECASE,
)
_EXCLUDE = re.compile(
    r"\b(?:reupload|re-upload|reposted?|reaction\s+archive|"
    r"compilation|tribute|interview|discussion|watch\s+party|"
    r"podcast|highlights?|teaser|trailer|official\s+(?:music\s+)?video|"
    r"flew\s+to|meet(?:ing)?\s+missioned\s+souls|travel\s+(?:vlog|diary)|"
    r"behind\s+the\s+scenes|fan\s+meet|concert\s+announcement)\b",
    re.IGNORECASE,
)


def clear_reaction_title(video, *, subject="Missioned Souls"):
    """True only with explicit title evidence that the VOD is a reaction.

    A title saying only 'Missioned Souls Family Band Studio Cover' does NOT
    establish a reaction, unless description metadata also carries multiple
    explicit reaction hashtags. Neither source is visual content proof.
    """
    title = video.get("title", "")
    if not isinstance(title, str) or subject.lower() not in title.lower():
        return False
    if _EXCLUDE.search(title):
        return False
    if "paper dove" in title.lower() and "#missionedsouls" in title.lower():
        return False
    if _REACTION.search(title):
        return True
    # Many reactors copy the original song title verbatim and identify their
    # reaction using hashtags in the video description. Require MULTIPLE
    # explicit reaction tags; one loose hashtag is insufficient.
    description = video.get("description", "")
    if not isinstance(description, str):
        return False
    tags = set(re.findall(r"(?<![a-z0-9])#[a-z0-9]+", description.lower()))
    return len(tags & {
        "#reaction", "#reactionvideo", "#musicianreacts", "#musicreaction",
        "#firsttimehearing", "#reactingtomusic", "#firsttimereaction",
        "#firstreaction",
    }) >= 2

def is_strong_reaction_candidate(video, *, subject="Missioned Souls"):
    """Publication-independent metadata evidence used by production VOD delivery.

    A band mention alone, unrelated vlog, archive or discussion never suffices.
    For ambiguous song-title uploads, multiple explicit reaction hashtags can
    qualify, but even strong metadata is not proof of video contents.
    """
    return clear_reaction_title(video, subject=subject)
