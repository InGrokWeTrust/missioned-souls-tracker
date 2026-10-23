"""Original bounded, paginated YouTube search for Missioned Souls.

Query only the quoted band name, retrieving successive pages of newest
results. The search budget remains capped at the original number of
search.list requests; no rotating or added keyword searches.
"""


def discovery_queries(subject, search_budget):
    """Yield (quoted_subject_query, zero_based_page) up to the search budget."""
    search_budget = max(1, min(5, int(search_budget)))
    subject = str(subject).strip().replace('"', "")
    if not subject:
        raise ValueError("Discovery subject cannot be blank")
    for page in range(search_budget):
        yield f'"{subject}"', page
