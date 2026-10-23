"""Predictable YouTube discovery search budget, with reaction-focused coverage.

The original two-page broad search remains; one additional targeted search
is used when budget permits. Each search.list call costs 100 quota units.
"""
def discovery_queries(subject, broad_pages):
    broad_pages = max(1, min(5, int(broad_pages)))
    for page in range(broad_pages):
        yield f'"{subject}"', page
    # One targeted page: avoid exploding quota through a combinatorial search.
    yield f'"{subject}" reaction', 0
