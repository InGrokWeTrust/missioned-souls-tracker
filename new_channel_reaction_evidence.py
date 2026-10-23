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



def plausible_reaction_title(title, *, subject="Missioned Souls"):
    """Low-cost first-stage candidate filter; NOT a delivery authorization."""
    if not isinstance(title, str) or _EXCLUDE.search(title):
        return False
    return subject.lower() in title.lower() or bool(_REACTION.search(title))


def subject_reaction_search_hit(snippet, *, subject="Missioned Souls"):
    """Accept description-only subject mentions only with an explicit reaction title.

    The subject must appear near the beginning of the YouTube description,
    rather than only in an unrelated link/tag list. Final metadata verification
    still decides whether this is a real reaction.
    """
    title = snippet.get("title", "")
    description = snippet.get("description", "")
    if not isinstance(title, str):
        return False
    if subject.lower() in title.lower():
        return True
    if not plausible_reaction_title(title, subject=subject):
        return False
    return (isinstance(description, str)
            and subject.lower() in description[:600].lower()
            and bool(_REACTION.search(description[:600]))
            and not _EXCLUDE.search(title))


_REACTION_CHANNEL = re.compile(r"\\breact(?:s|ion|ions|or|ors)?\\b", re.IGNORECASE)
_QUESTION_TITLE = re.compile(
    r"^\\s*(?:is|are|did|do|does|can|could|will|would|was|were|has|have|how|what|why)\\b",
    re.IGNORECASE,
)


def clear_reaction_title(video, *, subject="Missioned Souls"):
    """Require clear title evidence, or multiple independent contextual signals.

    Question-style titles are accepted only from reaction-labelled channels
    with an explicit reaction hashtag; ordinary covers are still rejected.
    A title saying only 'Missioned Souls Family Band Studio Cover' does NOT
    establish a reaction, unless description metadata also carries multiple
    explicit reaction hashtags. Neither source is visual content proof.
    """
    title = video.get("title", "")
    if not isinstance(title, str):
        return False
    description = video.get("description", "")
    if subject.lower() not in title.lower():
        return subject_reaction_search_hit(video, subject=subject)
    if _EXCLUDE.search(title):
        return False
    if "paper dove" in title.lower() and "#missionedsouls" in title.lower():
        return False
    if _REACTION.search(title):
        return True
    # "Is MISSIONED SOULS Black Velvet As Good As the Original?" is a
    # confirmed reaction, but says neither "reacts" nor "reaction" in title.
    # Require a QUESTION, a reaction-labelled uploader, and a reaction tag
    # in the actual video's description: these three signals must agree.
    channel = video.get("channel") or video.get("channel_title") or ""
    if (isinstance(channel, str) and _QUESTION_TITLE.match(title)
            and "?" in title
            and _REACTION_CHANNEL.search(channel)
            and isinstance(description, str)):
        tags = set(re.findall(r"(?<![a-z0-9])#[a-z0-9]+", description.lower()))
        if tags & {"#reaction", "#reactionvideo", "#musicreaction", "#firsttimereaction"}:
            return True
    # Many reactors copy the original song title verbatim and identify their
    # reaction using hashtags in the video description. Require MULTIPLE
    # explicit reaction tags; one loose hashtag is insufficient.
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
