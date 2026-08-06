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


def test_lookup_endpoints_reachable_anonymously():
    """Every FSSAI lookup endpoint must answer anonymous POSTs (no login 302).

    The portal pages call these from inline JS for form prefill/autocomplete.
    If one silently drops out of ``public_endpoints``, an anonymous or
    expired-session POST is 302-redirected to the login page and the page
    JS fails silently on the non-JSON redirect body. Regression guard for
    ``case_file_generator.lookup_fssai_route`` being missed while every
    other lookup endpoint is public.
    """
    from app import create_app

    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    lookups = [
        (
            "case_file_generator.lookup_fssai_route",
            "/case_file_generator/lookup_fssai",
            {"license_no": "12820019000569"},
        ),
        (
            "adjudication.lookup_fssai_route",
            "/adjudication/lookup_fssai",
            {"license_no": "12820019000569"},
        ),
        (
            "inspection.lookup_fssai_route",
            "/inspection/lookup_fssai",
            {"fssai_license": "12820019000569"},
        ),
        (
            "sample.lookup_retailer",
            "/sample/lookup_retailer",
            {"retailer_fssai_license": "12820019000569"},
        ),
    ]
    for endpoint, path, payload in lookups:
        resp = client.post(path, json=payload)
        assert resp.status_code != 302, f"{endpoint} redirected anonymous lookup to login"
        assert resp.status_code == 200, f"{endpoint} unexpected status {resp.status_code}: {resp.data[:120]!r}"
        assert resp.is_json, f"{endpoint} did not return JSON"
