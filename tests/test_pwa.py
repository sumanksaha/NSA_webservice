"""Tests for the Progressive Web App feature (issue #70).

Covers:
  - Manifest route returns 200 + valid JSON with installability keys (when flag on)
  - Manifest route returns 404 when the flag is off
  - Icon static endpoints resolve with image content types
  - Root-scoped service worker route returns 200 + JS content type (flag on)
  - Service worker route returns 404 when the flag is off
  - CSP includes worker-src 'self' only when the flag is on
  - cfg.enable_pwa defaults to False (opt-in convention)

Test pattern follows tests/test_csp_headers.py: create_app() + test_client().
"""

import pytest


@pytest.fixture
def pwa_client(monkeypatch):
    """A test client with ENABLE_PWA=true."""
    from app import create_app

    monkeypatch.setenv("ENABLE_PWA", "true")
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def default_client():
    """A test client with ENABLE_PWA unset (default off)."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _csp(resp):
    return resp.headers.get("Content-Security-Policy", "")


# --- Config seam: default-off, opt-in convention ---


def test_enable_pwa_defaults_off():
    """cfg.enable_pwa must default to False (opt-in: only 'true' enables)."""
    from app.shared.config import cfg

    import os

    old = os.environ.pop("ENABLE_PWA", None)
    try:
        assert cfg.enable_pwa is False
    finally:
        if old is not None:
            os.environ["ENABLE_PWA"] = old


def test_enable_pwa_opt_in(monkeypatch):
    """Only the string 'true' enables the flag (opt-in convention)."""
    from app.shared.config import cfg

    for raw, expected in [
        ("true", True),
        ("TRUE", True),
        ("1", False),
        ("yes", False),
        ("false", False),
    ]:
        monkeypatch.setenv("ENABLE_PWA", raw)
        assert cfg.enable_pwa is expected, raw


# --- Web app manifest ---


class TestManifest:
    def test_manifest_returns_200_with_required_keys(self, pwa_client):
        resp = pwa_client.get("/manifest.json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data.get("name")
        assert data.get("short_name")
        assert data.get("start_url")
        assert data["display"] == "standalone"
        sizes = {icon["sizes"] for icon in data["icons"]}
        assert "192x192" in sizes
        assert "512x512" in sizes

    def test_manifest_includes_maskable_icons(self, pwa_client):
        resp = pwa_client.get("/manifest.json")
        data = resp.get_json()
        maskable = [i for i in data["icons"] if i.get("purpose") == "maskable"]
        assert len(maskable) >= 1, "manifest must include at least one maskable icon"

    def test_manifest_returns_404_when_disabled(self, default_client):
        resp = default_client.get("/manifest.json")
        assert resp.status_code == 404


# --- Icon endpoints ---


class TestIcons:
    def test_icon_192_resolves(self, pwa_client):
        resp = pwa_client.get("/static/icons/pwa-192.png")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")

    def test_icon_512_resolves(self, pwa_client):
        resp = pwa_client.get("/static/icons/pwa-512.png")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")

    def test_maskable_192_resolves(self, pwa_client):
        resp = pwa_client.get("/static/icons/pwa-192-maskable.png")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")

    def test_favicon_16_resolves(self, pwa_client):
        resp = pwa_client.get("/static/icons/pwa-16-favicon.png")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")


# --- Service worker ---


class TestServiceWorker:
    def test_sw_returns_200_with_js_content_type(self, pwa_client):
        resp = pwa_client.get("/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.content_type

    def test_sw_contains_network_first_guard(self, pwa_client):
        """The SW must network-first for authenticated/API routes."""
        resp = pwa_client.get("/sw.js")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "NETWORK_FIRST" in body or "network" in body.lower()
        assert "/auth/" in body or "/api/" in body

    def test_sw_returns_404_when_disabled(self, default_client):
        resp = default_client.get("/sw.js")
        assert resp.status_code == 404


# --- Offline fallback page ---


class TestOffline:
    def test_offline_returns_200_with_html(self, pwa_client):
        resp = pwa_client.get("/offline")
        assert resp.status_code == 200
        assert "text/html" in resp.content_type

    def test_offline_returns_404_when_disabled(self, default_client):
        resp = default_client.get("/offline")
        assert resp.status_code == 404


# --- CSP worker-src directive ---


class TestCSP:
    def test_csp_includes_worker_src_when_enabled(self, pwa_client):
        resp = pwa_client.get("/auth/login")
        csp = _csp(resp)
        assert "worker-src" in csp, f"CSP missing worker-src: {csp!r}"
        assert "'self'" in csp

    def test_csp_excludes_worker_src_when_disabled(self, default_client):
        resp = default_client.get("/auth/login")
        csp = _csp(resp)
        assert "worker-src" not in csp, f"CSP should not include worker-src when disabled: {csp!r}"


# --- Template injection (manifest link + SW registration) ---


class TestTemplateInjection:
    def test_base_template_includes_manifest_link_when_enabled(self, pwa_client):
        resp = pwa_client.get("/auth/login")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<link rel="manifest"' in html

    def test_base_template_includes_sw_registration_when_enabled(self, pwa_client):
        resp = pwa_client.get("/auth/login")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "/sw.js" in html
        assert "serviceWorker" in html

    def test_base_template_omits_manifest_when_disabled(self, default_client):
        resp = default_client.get("/auth/login")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<link rel="manifest"' not in html
        assert "/sw.js" not in html


# --- Favicon (gated behind ENABLE_PWA, like other PWA assets) ---


class TestFavicon:
    def test_favicon_link_present_in_template(self, pwa_client):
        resp = pwa_client.get("/auth/login")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<link rel="icon"' in html

    def test_favicon_absent_when_pwa_off(self, default_client):
        resp = default_client.get("/auth/login")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<link rel="icon"' not in html

    def test_favicon_resolves(self, default_client):
        resp = default_client.get("/static/icons/pwa-32-favicon.png")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")
