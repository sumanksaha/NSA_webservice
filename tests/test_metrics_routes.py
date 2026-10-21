"""Tests for the ``/metrics`` route and the HTTP instrumentation hooks (OBS3).

Two properties are easy to get subtly wrong, and both are the reason this file
exists:

* the scrape path must be served by **FastAPI**, not swallowed by the Flask mount
  (FR-28). Registered after the mount, ``/metrics`` works in a unit test and 404s
  under uvicorn — which is the failure this test catches;
* a request must be counted on **exactly one** surface, never both (INV-3). The
  two halves never share state: the ASGI middleware counts only ``/api/v2/*``,
  which never reaches Flask.

Assertions read prometheus_client's child metrics directly, which is how the
client's own test suite works; the exposition-level checks live in
``tests/test_observability_metrics.py``.

See ``docs/OBSERVABILITY_SENTRY_PROMETHEUS_SPEC.md`` §8, §12 (AC-10…AC-18).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import sys
from typing import NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount

from app.observability import metrics as obs_metrics

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

SCRAPE_TOKEN = "scrape-t0ken"

#: A real Flask route containing an integer converter: the point is that the
#: *rule* is recorded and the interpolated ``4821`` never is.
FLASK_RULE = "/inspection/<int:inspection_id>/auditor-plan"
FLASK_PATH = "/inspection/4821/auditor-plan"


class AsgiApps(NamedTuple):
    """The two ASGI module instances this module tests against."""

    default: object
    enabled: object
    #: ``cfg.metrics_enabled`` as the *default* module saw it at import time.
    default_metrics_enabled: bool


# --------------------------------------------------------------------------- #
# Registry readers
# --------------------------------------------------------------------------- #
def _series(metric) -> list[dict[str, str]]:
    """Every child series of a metric, as ``{label: value}``."""
    names = metric._labelnames
    return [dict(zip(names, child._labelvalues)) for child in metric._metrics.values()]


def _counter_total(surface: str | None = None) -> float:
    """Sum of ``nsa_http_requests_total``, optionally for one surface."""
    total = 0.0
    for record, child in zip(_series(obs_metrics.HTTP_REQUESTS), obs_metrics.HTTP_REQUESTS._metrics.values()):
        if surface is None or record["surface"] == surface:
            total += child._value.get()
    return total


def _counter(**labels: str) -> float:
    """One ``nsa_http_requests_total`` series (0.0 when it does not exist)."""
    return obs_metrics.HTTP_REQUESTS.labels(**labels)._value.get()


def _in_progress(surface: str, route: str) -> float:
    return obs_metrics.HTTP_REQUESTS_IN_PROGRESS.labels(surface=surface, route=route)._value.get()


def _histogram_observations(child) -> float:
    """Total observations recorded on a histogram child.

    prometheus_client 0.26.0 keeps one ``Value`` per bucket on the child and
    derives the ``_count`` sample only when rendering, so the total number of
    observations is the sum of the buckets.
    """
    return sum(bucket.get() for bucket in child._buckets)


def _load_asgi(name: str):
    """Execute ``asgi.py`` afresh under a private module name.

    The real module registers ``/metrics`` at import time and only when metrics
    are configured, so the enabled path cannot be observed by importing ``asgi``
    twice — the module cache would hand back the first result.
    """
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "asgi.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def asgi_apps() -> AsgiApps:
    """The default ASGI module plus a freshly executed metrics-enabled copy.

    The default module is imported *before* the flag is set, and the flag's
    effective value at that moment is captured: assertions about "no metrics
    surface" must describe the deploy that did not opt in, not the env this test
    module leaves behind for its own use.
    """
    default_module = importlib.import_module("asgi")

    from app.shared.config import cfg

    default_metrics_enabled = bool(cfg.metrics_enabled)

    saved = {key: os.environ.get(key) for key in ("METRICS_ENABLED", "METRICS_TOKEN")}
    os.environ["METRICS_ENABLED"] = "true"
    os.environ["METRICS_TOKEN"] = SCRAPE_TOKEN
    try:
        enabled_module = _load_asgi("asgi_metrics_enabled")
        yield AsgiApps(default_module, enabled_module, default_metrics_enabled)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# --------------------------------------------------------------------------- #
# /metrics on the real module: registration, then the decision table
# --------------------------------------------------------------------------- #
class TestMetricsRouteRegistration:
    def test_default_deploy_has_no_metrics_surface(self, asgi_apps):
        """INV-5: without the flag, the app is exactly the app it was."""
        paths = [getattr(route, "path", None) for route in asgi_apps.default.app.routes]
        middleware = [entry.cls.__name__ for entry in asgi_apps.default.app.user_middleware]

        if asgi_apps.default_metrics_enabled:  # a developer's .env may genuinely opt in
            assert "/metrics" in paths
        else:
            assert "/metrics" not in paths
            assert "HttpMetricsMiddleware" not in middleware

    def test_enabled_deploy_registers_the_route_before_the_flask_mount(self, asgi_apps):
        """FR-28: after the mount, the route is unreachable."""
        routes = asgi_apps.enabled.app.routes
        metrics_index = next((i for i, route in enumerate(routes) if getattr(route, "path", None) == "/metrics"), None)
        mount_index = next((i for i, route in enumerate(routes) if isinstance(route, Mount)), None)

        assert metrics_index is not None, "GET /metrics was not registered"
        assert mount_index is not None, "expected the Flask mount"
        assert metrics_index < mount_index

    def test_enabled_deploy_installs_the_asgi_middleware(self, asgi_apps):
        middleware = [entry.cls.__name__ for entry in asgi_apps.enabled.app.user_middleware]

        assert "HttpMetricsMiddleware" in middleware

    def test_the_real_app_serves_the_scrape_path(self, asgi_apps):
        """End-to-end through the mounted app, not a stand-in."""
        client = TestClient(asgi_apps.enabled.app)

        response = client.get("/metrics", headers={"Authorization": f"Bearer {SCRAPE_TOKEN}"})

        assert response.status_code == 200
        assert response.headers["content-type"] == obs_metrics.CONTENT_TYPE_LATEST
        assert "nsa_http_requests_total" in response.text

    def test_the_canonical_scrape_path_does_not_redirect(self, asgi_apps):
        """FR-29 / Grafana: ``redirects are not supported``."""
        client = TestClient(asgi_apps.enabled.app)

        response = client.get("/metrics", headers={"Authorization": f"Bearer {SCRAPE_TOKEN}"}, follow_redirects=False)

        assert response.status_code == 200

    def test_the_scrape_path_is_never_counted(self, asgi_apps):
        """FR-33: metrics must not appear in their own HTTP series."""
        client = TestClient(asgi_apps.enabled.app)
        before = _counter_total()

        client.get("/metrics", headers={"Authorization": f"Bearer {SCRAPE_TOKEN}"})

        assert _counter_total() == before


class TestMetricsDecisionTable:
    """Spec §8.1 — the four rows, against a throwaway FastAPI app."""

    @pytest.fixture
    def scrape_client(self, asgi_apps, monkeypatch) -> TestClient:
        monkeypatch.setenv("METRICS_ENABLED", "true")
        monkeypatch.setenv("METRICS_TOKEN", SCRAPE_TOKEN)
        app = FastAPI()
        asgi_apps.default.register_metrics_route(app)
        return TestClient(app)

    def test_valid_bearer_returns_200_and_prometheus_text(self, scrape_client):
        obs_metrics.observe_http_request(
            surface="flask", method="GET", route="/scrape-probe", status=200, duration=0.01
        )

        response = scrape_client.get("/metrics", headers={"Authorization": f"Bearer {SCRAPE_TOKEN}"})

        assert response.status_code == 200
        assert response.headers["content-type"] == obs_metrics.CONTENT_TYPE_LATEST
        assert 'nsa_http_requests_total{method="GET",route="/scrape-probe"' in response.text

    @pytest.mark.parametrize(
        "header",
        [
            None,
            "Bearer wrong-token",
            "Bearer",
            "Bearer ",
            "Basic dXNlcjpwYXNz",
            SCRAPE_TOKEN,
            "bearer " + SCRAPE_TOKEN[:-1],
        ],
    )
    def test_missing_or_invalid_credentials_are_401(self, scrape_client, header):
        headers = {} if header is None else {"Authorization": header}

        response = scrape_client.get("/metrics", headers=headers)

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert "nsa_" not in response.text

    def test_lowercase_bearer_scheme_is_accepted(self, scrape_client):
        """RFC 7235: the scheme is case-insensitive."""
        response = scrape_client.get("/metrics", headers={"Authorization": f"bearer {SCRAPE_TOKEN}"})

        assert response.status_code == 200

    def test_blank_token_fails_closed_with_503(self, scrape_client, monkeypatch):
        monkeypatch.setenv("METRICS_TOKEN", "")

        response = scrape_client.get("/metrics", headers={"Authorization": "Bearer anything"})

        assert response.status_code == 503
        assert "nsa_" not in response.text

    def test_blank_token_logs_an_error(self, asgi_apps, scrape_client, monkeypatch):
        """SEC-6: a misconfiguration is loud, not silent."""
        monkeypatch.setenv("METRICS_TOKEN", "")
        captured: list[str] = []
        original_error = asgi_apps.default.logger.error

        def _spy(message, *args, **kwargs):
            captured.append(str(message))
            return original_error(message, *args, **kwargs)

        monkeypatch.setattr(asgi_apps.default.logger, "error", _spy)
        scrape_client.get("/metrics", headers={"Authorization": "Bearer anything"})

        assert any("misconfigured" in message for message in captured)

    def test_disabled_never_serves_exposition(self, asgi_apps, monkeypatch):
        """Belt and braces: the handler is total even when called directly."""
        monkeypatch.setenv("METRICS_ENABLED", "false")
        app = FastAPI()
        asgi_apps.default.register_metrics_route(app)

        response = TestClient(app).get("/metrics", headers={"Authorization": f"Bearer {SCRAPE_TOKEN}"})

        assert response.status_code == 404
        assert "nsa_" not in response.text

    def test_registering_the_route_adds_exactly_one_route(self, asgi_apps):
        """§2.1: this work adds no other route."""
        app = FastAPI()
        before = len(app.routes)

        asgi_apps.default.register_metrics_route(app)

        assert len(app.routes) == before + 1


# --------------------------------------------------------------------------- #
# The ASGI middleware, on a controlled app (FR-35, INV-3)
# --------------------------------------------------------------------------- #
def _instrumented_app(asgi_apps) -> FastAPI:
    app = FastAPI()
    app.add_middleware(asgi_apps.default.HttpMetricsMiddleware)

    @app.get("/api/v2/probe/{probe_id}")
    async def probe(probe_id: int) -> dict:
        return {"id": probe_id}

    @app.get("/api/v2/boom")
    async def boom() -> dict:
        raise RuntimeError("kaboom")

    @app.get("/api/v2/health")
    async def health() -> dict:
        return {"ok": True}

    return app


class TestAsgiMiddleware:
    def test_request_is_counted_once_with_the_route_rule(self, asgi_apps):
        client = TestClient(_instrumented_app(asgi_apps))
        before = _counter_total(surface="fastapi")

        response = client.get("/api/v2/probe/4821")

        assert response.status_code == 200
        assert _counter_total(surface="fastapi") - before == 1.0
        assert _counter(surface="fastapi", method="GET", route="/api/v2/probe/{probe_id}", status="200") >= 1.0

    def test_the_interpolated_identifier_never_becomes_a_label(self, asgi_apps):
        client = TestClient(_instrumented_app(asgi_apps))

        client.get("/api/v2/probe/4821")

        routes = [record["route"] for record in _series(obs_metrics.HTTP_REQUESTS) if record["surface"] == "fastapi"]
        assert routes
        assert all("4821" not in route for route in routes), routes

    def test_in_progress_returns_to_its_baseline(self, asgi_apps):
        client = TestClient(_instrumented_app(asgi_apps))
        before = _in_progress("fastapi", obs_metrics.PENDING)

        client.get("/api/v2/probe/1")

        assert _in_progress("fastapi", obs_metrics.PENDING) == before

    def test_health_is_not_counted(self, asgi_apps):
        client = TestClient(_instrumented_app(asgi_apps))
        before = _counter_total()

        response = client.get("/api/v2/health")

        assert response.status_code == 200
        assert _counter_total() == before

    def test_non_api_v2_paths_belong_to_flask(self, asgi_apps):
        client = TestClient(_instrumented_app(asgi_apps))
        before = _counter_total()

        client.get("/metrics")

        assert _counter_total() == before

    def test_unhandled_error_is_counted_as_500(self, asgi_apps):
        client = TestClient(_instrumented_app(asgi_apps), raise_server_exceptions=False)
        before = _counter_total(surface="fastapi")

        response = client.get("/api/v2/boom")

        assert response.status_code == 500
        assert _counter_total(surface="fastapi") - before == 1.0
        assert _counter(surface="fastapi", method="GET", route="/api/v2/boom", status="500") >= 1.0


# --------------------------------------------------------------------------- #
# The Flask hooks, through a factory-built app with metrics on (FR-34, FR-36)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def flask_metrics_app(tmp_path_factory):
    """A real Flask app built with ``METRICS_ENABLED`` set.

    The hooks are registered inside ``create_app()``, so the flag has to be in the
    environment *before* the factory runs — exactly as in a deploy.
    """
    saved = os.environ.get("METRICS_ENABLED")
    os.environ["METRICS_ENABLED"] = "true"
    try:
        from app import create_app

        app = create_app(db_uri=f"sqlite:///{tmp_path_factory.mktemp('obs_metrics') / 'test.db'}")
        app.config["TESTING"] = True
        yield app
    finally:
        if saved is None:
            os.environ.pop("METRICS_ENABLED", None)
        else:
            os.environ["METRICS_ENABLED"] = saved


class TestFlaskHooks:
    def test_redirect_is_counted_with_the_matched_rule(self, flask_metrics_app):
        """AC-15: the login gate's 302 still carries the resolved rule."""
        client = flask_metrics_app.test_client()
        before = _counter_total(surface="flask")

        response = client.get(FLASK_PATH)

        assert response.status_code == 302  # unauthenticated -> login
        assert _counter_total(surface="flask") - before == 1.0
        assert _counter(surface="flask", method="GET", route=FLASK_RULE, status="302") >= 1.0

    def test_no_label_contains_the_interpolated_identifier(self, flask_metrics_app):
        client = flask_metrics_app.test_client()

        client.get(FLASK_PATH)

        routes = [record["route"] for record in _series(obs_metrics.HTTP_REQUESTS) if record["surface"] == "flask"]
        assert routes
        assert all("4821" not in route for route in routes), routes
        assert all(route.startswith("/") or route == "unmatched" for route in routes), routes

    def test_a_404_lands_in_the_unmatched_series(self, flask_metrics_app):
        """A burst of 404s is visible without one series per URL."""
        client = flask_metrics_app.test_client()
        before = _counter(surface="flask", method="GET", route="unmatched", status="404")

        response = client.get("/definitely-not-a-route-xyz")

        assert response.status_code == 404
        assert _counter(surface="flask", method="GET", route="unmatched", status="404") == before + 1

    @pytest.mark.parametrize("path", ["/metrics", "/health", "/api/v2/health"])
    def test_skipped_paths_create_no_series(self, flask_metrics_app, path):
        """FR-33/FR-36: monitor pings must not flatten the HTTP rate."""
        client = flask_metrics_app.test_client()
        before = _counter_total()

        client.get(path)

        assert _counter_total() == before
        assert path not in {record["route"] for record in _series(obs_metrics.HTTP_REQUESTS)}

    def test_in_progress_returns_to_its_baseline(self, flask_metrics_app):
        client = flask_metrics_app.test_client()
        before = _in_progress("flask", FLASK_RULE)

        client.get(FLASK_PATH)

        assert _in_progress("flask", FLASK_RULE) == before

    def test_latency_is_observed_for_the_route(self, flask_metrics_app):
        client = flask_metrics_app.test_client()
        child = obs_metrics.HTTP_REQUEST_DURATION.labels(surface="flask", method="GET", route=FLASK_RULE)
        before_sum = child._sum.get()
        before_count = _histogram_observations(child)

        client.get(FLASK_PATH)

        assert child._sum.get() > before_sum
        assert _histogram_observations(child) == before_count + 1


# --------------------------------------------------------------------------- #
# Both halves at once, through the mounted app (INV-3: counted exactly once)
# --------------------------------------------------------------------------- #
class TestSingleCountingAcrossSurfaces:
    def test_a_flask_path_through_the_asgi_app_is_counted_only_on_flask(self, asgi_apps):
        client = TestClient(asgi_apps.enabled.app)
        before_flask = _counter_total(surface="flask")
        before_fastapi = _counter_total(surface="fastapi")

        response = client.get(FLASK_PATH, follow_redirects=False)

        assert response.status_code == 302
        assert _counter_total(surface="flask") - before_flask == 1.0
        assert _counter_total(surface="fastapi") - before_fastapi == 0.0

    def test_an_api_v2_path_is_counted_only_on_fastapi(self, asgi_apps):
        client = TestClient(asgi_apps.enabled.app)
        before_flask = _counter_total(surface="flask")
        before_fastapi = _counter_total(surface="fastapi")

        response = client.get("/api/v2/search")

        assert response.status_code == 200
        assert _counter_total(surface="fastapi") - before_fastapi == 1.0
        assert _counter_total(surface="flask") - before_flask == 0.0

    def test_the_slowest_route_is_counted_exactly_once(self, asgi_apps, monkeypatch):
        """The route that motivated putting /metrics on the ASGI edge (FR-28).

        ``prometheus-flask-exporter`` would never see this one: it is served by
        FastAPI and never reaches the Flask mount.
        """
        from app.rag.resilient import ResilientRAGPipeline

        def stub_pipeline(query, **kwargs):
            return {
                "query": query,
                "answer": "stub answer",
                "citations": [],
                "retrieved_chunks": [],
                "groundedness_score": 0.5,
                "hallucination_detected": False,
            }

        monkeypatch.setattr(
            asgi_apps.enabled,
            "get_rag_pipeline",
            lambda: ResilientRAGPipeline(pipeline_fn=stub_pipeline, fallback_fn=stub_pipeline),
        )
        client = TestClient(asgi_apps.enabled.app)
        before = _counter_total()

        response = client.post("/api/v2/rag/generate", json={"query": "Section 50 of FSS Act penalty"})

        assert response.status_code == 200
        assert _counter_total() - before == 1.0
        assert _counter(surface="fastapi", method="POST", route="/api/v2/rag/generate", status="200") >= 1.0

    def test_the_liveness_probe_is_counted_on_neither_surface(self, asgi_apps):
        client = TestClient(asgi_apps.enabled.app)
        before = _counter_total()

        response = client.get("/api/v2/health")

        assert response.status_code == 200
        assert _counter_total() == before
