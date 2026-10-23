"""Shared broad Missioned Souls name matching for Discovery and reactions.

Accept the band name in singular or plural without adding opinionated
reaction-keyword classifiers. Exact word boundaries avoid similarly named
unrelated terms (e.g., Missioned Soulmate).
"""
import re

_NAME = re.compile(r"(?<!\w)missioned\s+souls?(?!\w)", re.IGNORECASE)


def title_mentions_band(title, *, channel_name="Missioned Souls"):
    if not isinstance(title, str):
        return False
    if channel_name.strip().casefold() == "missioned souls":
        return _NAME.search(title) is not None
    # Other explicit CHANNEL_NAME overrides keep their pre-existing rule.
    return channel_name.casefold() in title.casefold()


def discovery_queries(channel_name, *, plural_pages=2, include_singular=True):
    """Keep the plural search pages; add only one independent singular page."""
    yield f'"{channel_name}"', plural_pages
    if include_singular and channel_name.strip().casefold() == "missioned souls":
        yield '"Missioned Soul"', 1
