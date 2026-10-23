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


_REACTION_CHANNEL = re.compile(r"\breact(?:s|ion|ions|or|ors)?\b", re.IGNORECASE)
_QUESTION_TITLE = re.compile(
    r"^\s*(?:is|are|did|do|does|can|could|will|would|was|were|has|have|how|what|why)\b",
    re.IGNORECASE,
)



# Some genuine reactors use a personal observation as the title instead of
# "reaction". Example: "HER SOLO WAS CRAZY !! | ... BY MISSIONED SOULS".
# The opening description can independently establish that the creator is
# watching the band rather than uploading their own performance.
_FIRST_PERSON_VIEWING = re.compile(
    r"\b(?:i|we)\s+(?:(?:am|are|'m|'re)\s+)?"
    r"(?:checking\s+out|check(?:ing)?\s+out|watch(?:ing)?|listen(?:ing)?\s+to|"
    r"react(?:ing)?\s+to)\b",
    re.IGNORECASE,
)
_COMMENTARY_TITLE = re.compile(
    r"\b(?:i|we|my|our)\b"
    r"|\b(?:crazy|insane|shocked|speechless|unbelievable|amazing|"
    r"incredible|mind\s+blown|goosebumps|wow|whoa|blew\s+me\s+away)\b",
    re.IGNORECASE,
)
_SELF_PERFORMED = re.compile(
    r"\b(?:our|my)\s+(?:(?:own|original|live)\s+)?(?:cover|performance|version)\b"
    r"|\b(?:we|i)\s+(?:performed|recorded|played|covered)\s+(?:this|the|it)\b"
    r"|\b(?:performed|sung|recorded|played)\s+by\s+(?:me|us)\b",
    re.IGNORECASE,
)


def creator_describes_watching_subject(video, *, subject="Missioned Souls"):
    """Require an explicit first-person viewing claim near the named subject.

    A generic "check out my channel" link or a detached #MissionedSouls tag
    never qualifies. This is metadata evidence, not video-content proof.
    """
    description = video.get("description", "")
    if not isinstance(description, str) or not isinstance(subject, str):
        return False
    intro = description[:600]
    if _SELF_PERFORMED.search(intro):
        return False
    mentions = [m.start() for m in re.finditer(re.escape(subject), intro, re.IGNORECASE)]
    if not mentions:
        return False
    for viewing in _FIRST_PERSON_VIEWING.finditer(intro):
        if any(0 <= mention - viewing.end() <= 175 for mention in mentions):
            return True
    return False


def contextual_reaction_evidence(video, *, subject="Missioned Souls"):
    """Accept an explicit viewing claim for the named subject, never a band tag alone.

    Band-in-title cases require a genuine first-person watching statement in
    the video description. Song-only title cases additionally require
    commentary in the title or a reaction-labelled uploader.
    """
    title = video.get("title", "")
    if not isinstance(title, str) or _EXCLUDE.search(title):
        return False
    if not creator_describes_watching_subject(video, subject=subject):
        return False
    if subject.lower() in title.lower():
        return True
    channel = video.get("channel") or video.get("channel_title") or ""
    return (bool(_COMMENTARY_TITLE.search(title))
            or (isinstance(channel, str) and bool(_REACTION_CHANNEL.search(channel))))


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
        return (subject_reaction_search_hit(video, subject=subject)
                or contextual_reaction_evidence(video, subject=subject))
    if _EXCLUDE.search(title):
        return False
    if "paper dove" in title.lower() and "#missionedsouls" in title.lower():
        return False
    if _REACTION.search(title):
        return True
    # Explicit band+reaction compound hashtags are otherwise missed by the
    # word-boundary matcher: #MissionedSoulsReaction is a strong title signal.
    compound_subject = re.sub(r"[^a-z0-9]", "", subject.lower())
    if compound_subject and re.search(
        rf"(?<![a-z0-9])#{re.escape(compound_subject)}(?:reactions?|reacts?|reacting)\b",
        title, re.IGNORECASE,
    ):
        return True
    if contextual_reaction_evidence(video, subject=subject):
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

def requires_expanded_evidence(video, *, subject="Missioned Souls"):
    """Identify newly accepted title formats, to cap first-time historical catch-up.

    Previously accepted reaction-title and multi-hashtag cases keep their
    established queuing rules. Only new description/question-style paths are
    subject to the stricter recent-upload window.
    """
    if not clear_reaction_title(video, subject=subject):
        return False
    title = video.get("title", "")
    description = video.get("description", "")
    if subject.lower() not in title.lower():
        return True
    if _REACTION.search(title):
        return False
    if not isinstance(description, str):
        return True
    tags = set(re.findall(r"(?<![a-z0-9])#[a-z0-9]+", description.lower()))
    legacy_tags = {
        "#reaction", "#reactionvideo", "#musicianreacts", "#musicreaction",
        "#firsttimehearing", "#reactingtomusic", "#firsttimereaction", "#firstreaction",
    }
    return len(tags & legacy_tags) < 2
