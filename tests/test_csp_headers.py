"""Regression tests: Talisman double-init must not clobber the CSP.

2026-08-25: a leftover second ``talisman.init_app(app, force_https=False)``
in ``create_app()`` (after the fully-configured one) reset the
Content-Security-Policy to flask-talisman's built-in default
(``default-src 'self'; object-src 'none'``), stripping
``script-src 'unsafe-inline'``. Consequences observed live:

- Every inline ``<script>`` block in the Jinja templates was blocked:
  the FSSAI/CE license-lookup handlers were never defined, so the
  Lookup buttons did nothing and form fields never populated.
- ``window.CASE_ID`` on the document editor page stayed undefined;
  editor.js fetched ``/document_viewer/saved//petition`` (empty ID -> 404)
  and inline Quill bootstrap code was dead.

These tests pin the served CSP header so the custom policy survives.
"""

import pytest


@pytest.fixture
def client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _csp(resp):
    return resp.headers.get("Content-Security-Policy", "")


class TestCSPHeaders:
    def test_login_page_serves_custom_csp(self, client):
        resp = client.get("/auth/login")
        assert resp.status_code == 200
        csp = _csp(resp)
        # The custom dict declares these directives; the talisman default
        # policy ('default-src "self"; object-src "none"') does not.
        assert "script-src" in csp, f"CSP lost custom directives: {csp!r}"
        assert "style-src" in csp, f"CSP lost custom directives: {csp!r}"

    def test_inline_scripts_are_allowed(self, client):
        """Inline <script> must be allowed — all lookup/editor JS relies on it."""
        resp = client.get("/auth/login")
        csp = _csp(resp)
        script_directive = ""
        for part in csp.split(";"):
            part = part.strip()
            if part.startswith("script-src"):
                script_directive = part
                break
        assert "'unsafe-inline'" in script_directive, (
            f"script-src lost 'unsafe-inline': {csp!r}"
        )

    def test_default_object_src_only_policy_is_gone(self, client):
        """Exact default-policy fingerprint that caused the outage."""
        resp = client.get("/auth/login")
        csp = _csp(resp)
        assert csp.strip() != "default-src 'self'; object-src 'none'"
