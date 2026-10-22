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
    r"podcast|highlights?|teaser|trailer|official\s+(?:music\s+)?video)\b",
    re.IGNORECASE,
)


def clear_reaction_title(video, *, subject="Missioned Souls"):
    """True only with explicit title evidence that the VOD is a reaction.

    A title saying only 'Missioned Souls Family Band Studio Cover' does NOT
    establish that the uploader is reacting to the cover.
    """
    title = video.get("title", "")
    if not isinstance(title, str) or subject.lower() not in title.lower():
        return False
    if _EXCLUDE.search(title):
        return False
    if "paper dove" in title.lower() and "#missionedsouls" in title.lower():
        return False
    return bool(_REACTION.search(title))
