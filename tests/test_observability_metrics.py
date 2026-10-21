"""Tests for the Prometheus metric catalogue (``app/observability/metrics.py``).

Everything here runs **without** a Flask app context: the module is deliberately
config-free and context-free so a scraper-shaped process can import it (FR-27).

Assertions read the rendered exposition rather than the metric objects, so the
tests keep working across prometheus_client internals.

See ``docs/OBSERVABILITY_SENTRY_PROMETHEUS_SPEC.md`` §5.2, §5.3, §5.4, §7, §12.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.observability import metrics

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Every family the spec's catalogue requires (§5.2 events, §5.3 snapshots).
#: A scrape endpoint is useless if a family is silently absent, and a name typo
#: stays invisible until a dashboard goes blank — so the list is explicit.
REQUIRED_FAMILIES: dict[str, str] = {
    "nsa_http_requests_total": "counter",
    "nsa_http_request_duration_seconds": "histogram",
    "nsa_http_requests_in_progress": "gauge",
    "nsa_rag_queries_total": "counter",
    "nsa_rag_retrieval_duration_seconds": "histogram",
    "nsa_rag_pipeline_duration_seconds": "histogram",
    "nsa_llm_generation_duration_seconds": "histogram",
    "nsa_llm_generations_total": "counter",
    "nsa_llm_tokens_total": "counter",
    "nsa_rag_hallucinations_total": "counter",
    "nsa_rag_degraded_total": "counter",
    "nsa_qstash_tasks_total": "counter",
    "nsa_qstash_task_duration_seconds": "histogram",
    "nsa_csp_violations_total": "counter",
    "nsa_rag_groundedness_avg": "gauge",
    "nsa_rag_hallucination_rate": "gauge",
    "nsa_rag_queries_24h": "gauge",
    "nsa_backup_last_success_timestamp_seconds": "gauge",
    "nsa_db_pool_checkedout": "gauge",
    "nsa_build_info": "gauge",
}

#: Prefixes prometheus_client's own default collectors register. Ours are the rest.
_CLIENT_OWNED_PREFIXES = ("python_", "process_", "platform_", "prometheus_")

_TYPE_LINE = re.compile(r"^# TYPE (\S+) (\S+)$", re.MULTILINE)
_SAMPLE_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? ([^ ]+)$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
_PROM_IMPORT = re.compile(r"^\s*(?:from|import)\s+prometheus_client\b", re.MULTILINE)


def _exposition() -> str:
    return metrics.render_metrics().decode("utf-8")


def _families(payload: str | None = None) -> dict[str, str]:
    """``{family_name: type}`` exactly as the exposition declares them."""
    return dict(_TYPE_LINE.findall(_exposition() if payload is None else payload))


def _samples(name: str) -> list[tuple[dict[str, str], float]]:
    """Every sample of *name* in the current exposition, as ``(labels, value)``."""
    found: list[tuple[dict[str, str], float]] = []
    for line in _exposition().splitlines():
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_LINE.match(line)
        if match is None or match.group(1) != name:
            continue
        found.append((dict(_LABEL.findall(match.group(2) or "")), float(match.group(3))))
    return found


def _value(name: str, **labels: str) -> float | None:
    """The value of a sample, or ``None`` when the series does not exist yet."""
    for sample_labels, value in _samples(name):
        if all(sample_labels.get(key) == expected for key, expected in labels.items()):
            return value
    return None


def _http_requests(**labels: str) -> float:
    """``nsa_http_requests_total`` for one label set (0.0 when never recorded)."""
    return _value("nsa_http_requests_total", **labels) or 0.0


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
class TestMetricCatalogue:
    def test_every_spec_family_is_defined_with_the_declared_type(self):
        families = _families()

        wrong = {
            name: (families.get(name), kind) for name, kind in REQUIRED_FAMILIES.items() if families.get(name) != kind
        }

        assert not wrong, f"family missing or mistyped (got, expected): {wrong}"

    def test_every_application_family_is_nsa_prefixed(self):
        """INV-9: one namespace, so a series can be attributed at a glance."""
        ours = [name for name in _families() if not name.startswith(_CLIENT_OWNED_PREFIXES)]

        assert ours, "expected at least one application metric in the registry"
        assert all(name.startswith("nsa_") for name in ours), ours

    def test_counter_families_end_in_total(self):
        """OpenMetrics convention; INV-8 pairs it with 'counters never decrease'."""
        offenders = [
            name
            for name, kind in _families().items()
            if kind == "counter" and name.startswith("nsa_") and not name.endswith("_total")
        ]

        assert not offenders, offenders

    def test_no_family_is_exported_twice(self):
        """A duplicated family is the classic multi-registry mistake (§5.4)."""
        declared = _TYPE_LINE.findall(_exposition())

        assert len(declared) == len(set(declared)), declared

    def test_histogram_buckets_reach_past_the_client_default(self):
        """§5.6: the 10 s default is below a real answer's latency."""
        metrics.RAG_PIPELINE_DURATION.labels("v2").observe(0.5)
        metrics.LLM_GENERATION_DURATION.labels("probe-model").observe(1.0)

        pipeline = {labels["le"] for labels, _ in _samples("nsa_rag_pipeline_duration_seconds_bucket")}
        llm = {labels["le"] for labels, _ in _samples("nsa_llm_generation_duration_seconds_bucket")}

        assert {"0.25", "1.0", "30.0", "120.0"}.issubset(pipeline), pipeline
        assert {"0.1", "1.0", "21.0", "55.0"}.issubset(llm), llm


# --------------------------------------------------------------------------- #
# Label allowlists (§7)
# --------------------------------------------------------------------------- #
class TestLabelAllowlists:
    @pytest.mark.parametrize("value", ["flask", "fastapi"])
    def test_surface_allowlist_accepts_the_two_surfaces(self, value):
        assert metrics.surface_label(value) == value

    def test_surface_allowlist_rejects_anything_else(self):
        assert metrics.surface_label("django") == "other"
        assert metrics.surface_label(None) == "other"

    def test_method_allowlist_uppercases_and_rejects_verbs(self):
        assert metrics.method_label("get") == "GET"
        assert metrics.method_label("PATCH") == "PATCH"
        assert metrics.method_label("BREW") == "other"

    def test_route_label_keeps_rules_and_refuses_junk(self):
        """V-3: a rule is recorded; a resolved path never is."""
        assert metrics.route_label("/inspection/<int:inspection_id>") == "/inspection/<int:inspection_id>"
        assert metrics.route_label(metrics.PENDING) == metrics.PENDING
        assert metrics.route_label(None) == "unmatched"
        assert metrics.route_label("") == "unmatched"
        assert metrics.route_label("no-leading-slash") == "unmatched"
        assert metrics.route_label("/" + "x" * (metrics.MAX_ROUTE_LENGTH + 1)) == "unmatched"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(200, "200"), ("404", "404"), (599, "599"), (99, "other"), (600, "other"), (None, "other"), ("nope", "other")],
    )
    def test_status_allowlist(self, raw, expected):
        assert metrics.status_label(raw) == expected


# --------------------------------------------------------------------------- #
# Path classification — what each surface owns (FR-34…FR-36, INV-3)
# --------------------------------------------------------------------------- #
class TestPathClassification:
    @pytest.mark.parametrize("path", ["/api/v2/rag/generate", "/api/v2/docs"])
    def test_fastapi_owns_api_v2(self, path):
        assert metrics.is_countable_path(path) is True
        assert metrics.is_flask_countable_path(path) is False

    @pytest.mark.parametrize("path", ["/metrics", "/health", "/api/v2/health"])
    def test_never_counted_paths(self, path):
        """FR-33 and friends: a 10 s liveness ping must not flatten the graph."""
        assert metrics.is_countable_path(path) is False
        assert metrics.is_flask_countable_path(path) is False

    @pytest.mark.parametrize("path", ["/inspection/4821", "/", "/auth/login"])
    def test_flask_owns_everything_else(self, path):
        assert metrics.is_flask_countable_path(path) is True
        assert metrics.is_countable_path(path) is False


# --------------------------------------------------------------------------- #
# Recording and exposition (§5.4)
# --------------------------------------------------------------------------- #
class TestRecording:
    def test_counter_and_histogram_are_recorded_under_allowlisted_labels(self):
        labels = {"surface": "flask", "method": "GET", "route": "/probe/<int:id>"}
        before = _http_requests(**labels, status="200")
        before_count = _value("nsa_http_request_duration_seconds_count", **labels) or 0.0
        before_sum = _value("nsa_http_request_duration_seconds_sum", **labels) or 0.0

        metrics.observe_http_request(surface="flask", method="get", route="/probe/<int:id>", status=200, duration=0.42)

        assert _http_requests(**labels, status="200") == before + 1
        assert _value("nsa_http_request_duration_seconds_count", **labels) == before_count + 1
        assert _value("nsa_http_request_duration_seconds_sum", **labels) == pytest.approx(before_sum + 0.42)

    def test_hostile_labels_land_in_the_other_bucket(self):
        """A rogue value must not create a series — that is the whole allowlist.

        The registry is process-global, so every assertion here is a delta.
        """
        hostile = {"surface": "other", "method": "other", "route": "unmatched", "status": "other"}
        before = _http_requests(**hostile)

        metrics.observe_http_request(surface="kafka", method="BREW", route=None, status="999", duration=0.1)

        assert _http_requests(**hostile) == before + 1
        assert _http_requests(surface="kafka") == 0.0
        assert _http_requests(route="999") == 0.0

    def test_in_progress_gauge_returns_to_its_previous_value(self):
        before = _value("nsa_http_requests_in_progress", surface="fastapi", route=metrics.PENDING) or 0.0

        metrics.http_request_started("fastapi", metrics.PENDING)
        assert _value("nsa_http_requests_in_progress", surface="fastapi", route=metrics.PENDING) == before + 1
        metrics.http_request_finished("fastapi", metrics.PENDING)

        assert _value("nsa_http_requests_in_progress", surface="fastapi", route=metrics.PENDING) == before


class TestRenderMetrics:
    def test_scraping_does_not_reset_or_duplicate_anything(self):
        """The registry is process-global: assert deltas, not absolutes."""
        labels = {"surface": "flask", "method": "GET", "route": "/scrape-probe", "status": "200"}
        before = _http_requests(**labels)

        metrics.HTTP_REQUESTS.labels(**labels).inc()

        first = _families()
        assert _http_requests(**labels) == before + 1
        second = _families()

        assert first == second
        assert _http_requests(**labels) == before + 1

    def test_multiprocess_branch_ignores_the_in_process_registry(self, tmp_path, monkeypatch):
        """§5.4: the fresh registry exists precisely to avoid a double export."""
        metrics.HTTP_REQUESTS.labels("flask", "GET", "/multiproc-probe", "200").inc()
        monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

        payload = metrics.render_metrics()

        assert b"nsa_http_requests_total{" not in payload
        declared = _TYPE_LINE.findall(payload.decode("utf-8"))
        assert len(declared) == len(set(declared))

    def test_multiprocess_branch_is_evaluated_on_every_call(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
        assert b"nsa_http_requests_total" not in metrics.render_metrics()

        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR")

        assert b"nsa_http_requests_total" in metrics.render_metrics()

    def test_render_returns_bytes(self):
        assert isinstance(metrics.render_metrics(), bytes)


# --------------------------------------------------------------------------- #
# Ownership guards (FR-10, FR-27)
# --------------------------------------------------------------------------- #
class TestOwnershipGuards:
    def test_metrics_module_is_config_and_context_free(self):
        """It must be importable by a scraper-shaped process (FR-27)."""
        source = pathlib.Path(metrics.__file__).read_text(encoding="utf-8")

        assert "current_app" not in source
        assert "from app.shared.config" not in source
        assert "from app import" not in source

    def test_prometheus_client_is_imported_by_exactly_one_module(self):
        """FR-10: one module constructs metrics; two importers is how the
        allowlists get bypassed."""
        offenders = []
        candidates = [*(REPO_ROOT / "app").rglob("*.py"), REPO_ROOT / "asgi.py"]
        for path in candidates:
            if path == pathlib.Path(metrics.__file__):
                continue
            if _PROM_IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO_ROOT)))

        assert not offenders, (
            f"prometheus_client is owned by app/observability/metrics.py, but imported by: {offenders}"
        )
