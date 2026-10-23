"""Fixed-budget YouTube searches: broaden coverage without extra search.list calls.

At the default two pages, alternate:
  slot A: two pages of the broad search
  slot B: one broad page plus one focused reaction search.
One search.list call costs 100 YouTube API quota units.
"""

def discovery_queries(subject, broad_pages, *, targeted=False):
    pages = max(1, min(5, int(broad_pages)))
    if targeted and pages > 1:
        for page in range(pages - 1):
            yield f'"{subject}"', page
        yield f'"{subject}" reaction', 0
    else:
        for page in range(pages):
            yield f'"{subject}"', page
