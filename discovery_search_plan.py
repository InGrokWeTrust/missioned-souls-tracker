"""Simple, quota-bounded YouTube discovery searches for Missioned Souls.

Always search the band name on every run. Use any remaining existing search
requests for one or more reaction-word variations. Cycle through variations
across two-hour discovery runs; no extra requests or complicated Boolean
expressions are needed. The returned search hits still require downstream
reaction verification before they can be posted to Discord.
"""

REACTION_KEYWORDS = ("reaction", "react", "reactor", "reacting")


def discovery_queries(subject, search_budget, *, rotation_slot=0):
    """Yield distinct (query, 0) pairs, never more than the existing budget.

    With the default budget of two calls per run, one is the broad subject
    query and one rotates reaction keywords once per two-hour slot. A higher
    configured budget uses successive distinct keywords in the same cycle.
    """
    search_budget = max(1, min(5, int(search_budget)))
    subject = str(subject).strip().replace('"', "")
    if not subject:
        raise ValueError("Discovery subject cannot be blank")

    yield f'"{subject}"', 0
    for offset in range(search_budget - 1):
        keyword = REACTION_KEYWORDS[(int(rotation_slot) + offset) % len(REACTION_KEYWORDS)]
        yield f'"{subject}" {keyword}', 0
