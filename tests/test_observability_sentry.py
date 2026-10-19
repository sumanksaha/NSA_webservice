"""Tests for Sentry error tracking (``app/observability/sentry.py``).

The sharp edges covered here are verified behaviours of ``sentry-sdk==2.72.0``,
not opinions about it: ``dsn=None`` falls back to ``$SENTRY_DSN``, a malformed
DSN raises ``BadDsn`` out of ``Transport.__init__``, a labelled ``EventScrubber``
is **not** recursive by default, and there is no ``error_sample_rate`` option.

Every test either leaves the DSN blank or replaces ``sentry_sdk.init`` with a
recording spy, so the fast shard still performs no network I/O.

See ``docs/OBSERVABILITY_SENTRY_PROMETHEUS_SPEC.md`` §3.3, §5.1, §9, §12.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import sentry_sdk
from flask import Flask

from app.observability import sentry as sentry_mod

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

VALID_DSN = "https://abc123@o1.ingest.sentry.io/42"


class _InitSpy:
    """Stand-in for ``sentry_sdk.init`` that records instead of dialling out."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.calls.append(kwargs)


class FakeUser:
    """Minimal stand-in for the Flask-Login ``current_user`` proxy."""

    def __init__(self, authenticated: bool = True, is_admin: bool = False) -> None:
        self.is_authenticated = authenticated
        self.is_admin = is_admin


@pytest.fixture(autouse=True)
def _sentry_isolation(monkeypatch):
    """Start every test uninitialised, with no Sentry env leaked in."""
    for key in ("SENTRY_DSN", "SENTRY_ENVIRONMENT", "SENTRY_TRACES_SAMPLE_RATE", "RENDER", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sentry_mod, "_initialised", False)


@pytest.fixture
def init_spy(monkeypatch) -> _InitSpy:
    spy = _InitSpy()
    monkeypatch.setattr(sentry_sdk, "init", spy)
    return spy


def _hostile_event() -> dict:
    """An event carrying exactly what must never leave the process."""
    return {
        "event_id": "0f1e2d3c",
        "request": {
            "url": "https://app.example/api/rag/generate",
            "data": {"query": "Is Acme Foods compliant?", "license_no": "10012345678901", "name": "Suman Saha"},
            "cookies": {"session": "s3cret-session"},
            "query_string": "q=Is+Acme+Foods+compliant",
            "headers": {"Authorization": "Bearer tok_live", "User-Agent": "pytest"},
        },
        "user": {"id": "7", "username": "officer7", "email": "officer@example.gov.in", "ip_address": "10.0.0.4"},
        "extra": {"license_number": "10012345678901", "fso_name": "Acme Foods", "safe": "keep-me"},
        "contexts": {"audit": {"actor_role": "admin", "chain": [{"fso_name": "Acme Foods"}]}},
        "tags": {"request_id": "req-123", "address": "12 Main Road"},
    }


# ---------------------------------------------------------------------------
# init: the off switch, and the kwargs that must be exact
# ---------------------------------------------------------------------------


def test_blank_dsn_never_calls_init(init_spy):
    """D4: no DSN => no init, no transport, no network."""
    sentry_mod.init_sentry()

    assert init_spy.calls == []
    assert sentry_mod._initialised is False


def test_blank_config_dsn_beats_the_sdk_env_fallback(init_spy, monkeypatch):
    """The resolved config says "not configured"; the SDK must never see it.

    Proves ``sentry_sdk.client._get_options``'s ``os.environ["SENTRY_DSN"]``
    fallback is unreachable from here — skipping ``init`` is what makes the
    blank DSN a true off switch.
    """
    monkeypatch.setenv("SENTRY_DSN", VALID_DSN)
    app = Flask(__name__)
    app.config["SENTRY_DSN"] = ""  # seeded config wins inside an app context

    with app.app_context():
        sentry_mod.init_sentry()

    assert init_spy.calls == []


def test_configured_dsn_inits_with_exactly_the_spec_kwargs(init_spy, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", VALID_DSN)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "deadbeef")

    sentry_mod.init_sentry()

    assert len(init_spy.calls) == 1
    kwargs = init_spy.calls[0]
    assert kwargs["dsn"] == VALID_DSN
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["release"] == "nsa-webservice@deadbeef"
    assert kwargs["traces_sample_rate"] == 0.0
    assert "traces_sampler" not in kwargs  # INV-11: tracing stays off
    assert kwargs["environment"] == "development"  # RENDER unset
    assert kwargs["before_send"] is sentry_mod.before_send
    assert sentry_mod._initialised is True


def test_environment_is_production_on_render_and_release_falls_back(init_spy, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", VALID_DSN)
    monkeypatch.setenv("RENDER", "true")

    sentry_mod.init_sentry()

    kwargs = init_spy.calls[0]
    assert kwargs["environment"] == "production"
    assert kwargs["release"] == "nsa-webservice@dev"


def test_explicit_environment_wins_over_render_detection(init_spy, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", VALID_DSN)
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")

    sentry_mod.init_sentry()

    assert init_spy.calls[0]["environment"] == "staging"


def test_malformed_dsn_warns_and_stays_disabled(monkeypatch):
    """A typo in a dashboard field must not take a deploy down.

    Deliberately uses the *real* ``sentry_sdk.init``: ``BadDsn`` is raised out of
    ``Transport.__init__``, and the module must absorb it. The warning is
    captured by spying on the logger - ``caplog`` is unreliable here because the
    app's logging config suppresses propagation (see
    ``tests/test_rag_ui_gaps.py:262``).
    """
    monkeypatch.setenv("SENTRY_DSN", "not-a-dsn")

    warnings_seen: list[tuple[str, bool]] = []
    original_warning = sentry_mod.logger.warning

    def _spy(msg, *args, **kwargs):
        warnings_seen.append((str(msg), bool(kwargs.get("exc_info"))))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(sentry_mod.logger, "warning", _spy)

    sentry_mod.init_sentry()  # must not raise

    assert sentry_mod._initialised is False
    assert len(warnings_seen) == 1
    assert "Sentry init failed" in warnings_seen[0][0]
    assert warnings_seen[0][1] is True  # exc_info=True carries the transport traceback


def test_init_is_idempotent_per_process(init_spy, monkeypatch):
    """create_app() runs twice in the ASGI process; only one client may exist."""
    monkeypatch.setenv("SENTRY_DSN", VALID_DSN)

    sentry_mod.init_sentry()
    sentry_mod.init_sentry()

    assert len(init_spy.calls) == 1


def test_traces_sample_rate_default_is_errors_only(init_spy, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", VALID_DSN)
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")

    sentry_mod.init_sentry()
    assert init_spy.calls[0]["traces_sample_rate"] == 0.25

    monkeypatch.setattr(sentry_mod, "_initialised", False)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE")
    sentry_mod.init_sentry()
    assert init_spy.calls[1]["traces_sample_rate"] == 0.0


# ---------------------------------------------------------------------------
# before_send: noise out, identifiers out
# ---------------------------------------------------------------------------


def test_before_send_drops_body_cookies_and_identifiers():
    event = sentry_mod.before_send(_hostile_event(), {})

    assert event is not None
    request = event["request"]
    assert "data" not in request
    assert "cookies" not in request
    assert "query_string" not in request
    assert request["headers"]["Authorization"] == sentry_mod.REDACTED
    assert request["headers"]["User-Agent"] == "pytest"

    # Opaque identity is kept (id/username); contact details are not.
    assert event["user"] == {"id": "7", "username": "officer7"}

    assert event["extra"]["license_number"] == sentry_mod.REDACTED
    assert event["extra"]["fso_name"] == sentry_mod.REDACTED
    assert event["extra"]["safe"] == "keep-me"
    assert event["contexts"]["audit"]["chain"][0]["fso_name"] == sentry_mod.REDACTED
    assert event["tags"]["address"] == sentry_mod.REDACTED
    assert event["tags"]["request_id"] == "req-123"


def test_nothing_identifying_survives_serialisation():
    serialised = json.dumps(sentry_mod.before_send(_hostile_event(), {}))

    for secret in (
        "10012345678901",
        "Suman Saha",
        "Acme Foods",
        "s3cret-session",
        "tok_live",
        "officer@example.gov.in",
    ):
        assert secret not in serialised, f"{secret!r} leaked into the event"


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("werkzeug.exceptions", "NotFound"),
        ("werkzeug.exceptions", "Unauthorized"),
        ("flask_wtf.csrf", "CSRFError"),
    ],
)
def test_before_send_drops_noise_exceptions(module, name):
    event = {"exception": {"values": [{"module": module, "type": name}]}}

    assert sentry_mod.before_send(event, {}) is None


def test_before_send_keeps_real_errors():
    event = {
        "extra": {},
        "exception": {"values": [{"module": "app.services.premises_resolver", "type": "LookupError"}]},
    }

    assert sentry_mod.before_send(event, {}) is not None


# ---------------------------------------------------------------------------
# Request correlation
# ---------------------------------------------------------------------------


def test_bind_request_context_is_a_noop_while_disabled(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *args: calls.append(args))

    sentry_mod.bind_request_context(FakeUser(), "req-123")

    assert calls == []


def test_bind_request_context_sets_correlation_only(monkeypatch):
    """No audit body, chain hash, licence number or name reaches the scope."""
    monkeypatch.setattr(sentry_mod, "_initialised", True)
    tags: dict = {}
    contexts: dict = {}
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda key, value: tags.__setitem__(key, value))
    monkeypatch.setattr(sentry_sdk, "set_context", lambda key, value: contexts.__setitem__(key, value))

    sentry_mod.bind_request_context(FakeUser(is_admin=True), "req-123")

    assert tags == {"request_id": "req-123"}
    assert contexts == {"audit": {"actor_role": "admin"}}


def test_bind_request_context_omits_an_absent_request_id(monkeypatch):
    monkeypatch.setattr(sentry_mod, "_initialised", True)
    tags: dict = {}
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda key, value: tags.__setitem__(key, value))

    sentry_mod.bind_request_context(FakeUser(), "")

    assert tags == {}


def test_request_id_tag_is_length_capped(monkeypatch):
    monkeypatch.setattr(sentry_mod, "_initialised", True)
    tags: dict = {}
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda key, value: tags.__setitem__(key, value))

    sentry_mod.bind_request_context(FakeUser(), "x" * 500)

    assert len(tags["request_id"]) == 64


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (None, "anonymous"),
        (FakeUser(authenticated=False), "anonymous"),
        (FakeUser(), "user"),
        (FakeUser(is_admin=True), "admin"),
    ],
)
def test_actor_role_is_a_bounded_non_identifying_label(user, expected):
    assert sentry_mod.actor_role_for(user) == expected


# ---------------------------------------------------------------------------
# Policy guards
# ---------------------------------------------------------------------------


def test_no_capture_exception_was_added_to_call_sites():
    """The deliberate-degradation design is preserved (research §3).

    Sentry consumes signals the app already emits — the logging integration —
    rather than hundreds of new ``capture_exception()`` calls in ``except``
    blocks. The module that documents the rule is exempt from the grep.
    """
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.parent.name == "observability" and path.name == "sentry.py":
            continue
        if "capture_exception" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, f"capture_exception belongs in the logging integration, not call sites: {offenders}"


def test_no_pii_opt_in_exists_in_the_config_seam():
    """D5: PII capture is hard-wired off, deliberately not a flag."""
    from app.shared.config import cfg

    keys = {setting.key for setting in cfg.table()}

    assert not [key for key in keys if "PII" in key]
