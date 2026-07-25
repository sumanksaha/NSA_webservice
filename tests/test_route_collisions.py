"""Regression guard: assert no duplicate (method, path) pairs exist across all blueprints.

This test iterates over the entire Flask URL map after all blueprints are
registered and asserts that every (HTTP method, URL path) combination is
unique.  A duplicate would mean two different handler functions registered
for the same method+path, which is almost certainly a bug.
"""

from collections import Counter


def test_no_duplicate_routes():
    """Every (method, path) pair must be unique across the entire app."""
    from app import create_app
    app = create_app()

    rules = []
    for rule in app.url_map.iter_rules():
        # Flask automatically adds HEAD and OPTIONS for every route;
        # we only care about the explicitly-registered methods.
        methods = rule.methods - {"HEAD", "OPTIONS"}
        for method in sorted(methods):
            rules.append((method, rule.rule))

    dupes = [(m, p) for (m, p), count in Counter(rules).items() if count > 1]
    assert not dupes, f"Duplicate routes found: {dupes}"
